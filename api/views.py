"""ViewSets and views for the Restaurant Billing API."""

from decimal import Decimal
from django.utils import timezone
from django.db.models import Sum, Count, Q
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Category, MenuItem, Table, Customer, Order, OrderItem, Bill, KOT
from .serializers import (
    CategorySerializer,
    MenuItemSerializer,
    TableSerializer,
    CustomerSerializer,
    OrderReadSerializer,
    OrderWriteSerializer,
    BillSerializer,
    KOTSerializer,
)


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------

class CategoryViewSet(viewsets.ModelViewSet):
    """CRUD for menu categories."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name']
    ordering_fields = ['name', 'created_at']


# ---------------------------------------------------------------------------
# MenuItem
# ---------------------------------------------------------------------------

class MenuItemViewSet(viewsets.ModelViewSet):
    """CRUD for menu items with optional category filter."""

    queryset = MenuItem.objects.select_related('category').all()
    serializer_class = MenuItemSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'category__name']
    ordering_fields = ['name', 'price', 'created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        category_id = self.request.query_params.get('category')
        if category_id:
            qs = qs.filter(category_id=category_id)
        available = self.request.query_params.get('available')
        if available is not None:
            qs = qs.filter(is_available=available.lower() in ('true', '1', 'yes'))
        return qs

    @action(detail=False, methods=['get'], url_path='grouped')
    def grouped(self, request):
        """Return all available menu items grouped by category."""
        categories = Category.objects.prefetch_related('menu_items').all()
        result = []
        for cat in categories:
            items = cat.menu_items.filter(is_available=True)
            if items.exists():
                result.append({
                    'category': {'id': cat.id, 'name': cat.name},
                    'items': MenuItemSerializer(items, many=True).data,
                })
        return Response(result)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

class TableViewSet(viewsets.ModelViewSet):
    """CRUD for tables; list includes current occupancy and active order id."""

    queryset = Table.objects.all()
    serializer_class = TableSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name']
    ordering_fields = ['name', 'capacity']

    def get_queryset(self):
        qs = super().get_queryset()
        occupied = self.request.query_params.get('occupied')
        if occupied is not None:
            qs = qs.filter(is_occupied=occupied.lower() in ('true', '1', 'yes'))
        return qs


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

class CustomerViewSet(viewsets.ModelViewSet):
    """CRUD for customers with search by name or phone."""

    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['name', 'phone']
    ordering_fields = ['name', 'created_at']


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

class OrderViewSet(viewsets.ModelViewSet):
    """
    CRUD for orders plus custom actions:
      POST  /orders/{id}/generate_kot/   — create a KOT for the order
      POST  /orders/{id}/generate_bill/  — create or retrieve the bill
      GET   /orders/active_orders/       — list non-terminal orders
    """

    queryset = (
        Order.objects.select_related('table', 'customer', 'created_by')
        .prefetch_related('items__menu_item')
        .all()
    )
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['table__name', 'customer__name', 'status', 'order_type']
    ordering_fields = ['created_at', 'updated_at', 'status']

    def get_serializer_class(self):
        if self.action in ('create', 'update', 'partial_update'):
            return OrderWriteSerializer
        return OrderReadSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param.upper())
        order_type = self.request.query_params.get('order_type')
        if order_type:
            qs = qs.filter(order_type=order_type.upper())
        table_id = self.request.query_params.get('table')
        if table_id:
            qs = qs.filter(table_id=table_id)
        return qs

    # ------------------------------------------------------------------
    # Custom action: hold
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='hold')
    def hold(self, request, pk=None):
        """
        Put an order on hold.
        Status moves to HOLD — table stays occupied, order can be resumed later.
        """
        order = self.get_object()

        if order.status in (Order.Status.BILLED, Order.Status.PAID):
            return Response(
                {'detail': 'Cannot hold a billed/paid order.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if order.status == Order.Status.CANCELLED:
            return Response(
                {'detail': 'Cannot hold a cancelled order.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.status = Order.Status.HOLD
        order.save(update_fields=['status', 'updated_at'])
        serializer = OrderReadSerializer(order)
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # Custom action: generate_kot
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='generate_kot')
    def generate_kot(self, request, pk=None):
        """
        Create a KOT for the order.
        Captures a snapshot of current order items and advances status to KOT.
        """
        order = self.get_object()

        if order.status == Order.Status.CANCELLED:
            return Response(
                {'detail': 'Cannot generate KOT for a cancelled order.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if order.status in (Order.Status.BILLED, Order.Status.PAID):
            return Response(
                {'detail': 'Order is already billed/paid.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        items = order.items.select_related('menu_item').all()
        if not items.exists():
            return Response(
                {'detail': 'Order has no items. Add items before generating KOT.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        snapshot = [
            {
                'menu_item_id': item.menu_item.id,
                'name': item.menu_item.name,
                'quantity': item.quantity,
                'price': str(item.price),
                'notes': item.notes,
            }
            for item in items
        ]

        kot = KOT.objects.create(order=order, items_snapshot=snapshot)

        # Advance order status to KOT if still pending or on hold
        if order.status in (Order.Status.PENDING, Order.Status.HOLD):
            order.status = Order.Status.KOT
            order.save(update_fields=['status', 'updated_at'])

        serializer = KOTSerializer(kot)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Custom action: generate_bill
    # ------------------------------------------------------------------

    @action(detail=True, methods=['post'], url_path='generate_bill')
    def generate_bill(self, request, pk=None):
        """
        Create a bill for the order (or return the existing one).
        Accepts optional body: { tax_percent, discount, payment_mode, amount_received }
        """
        order = self.get_object()

        if order.status == Order.Status.CANCELLED:
            return Response(
                {'detail': 'Cannot bill a cancelled order.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # If bill already exists, delete it and regenerate with latest items
        # (handles Hold → add more items → SAVE flow)
        if hasattr(order, 'bill'):
            old_bill = order.bill
            # Recalculate with latest items
            items = order.items.all()
            if not items.exists():
                serializer = BillSerializer(old_bill)
                return Response(serializer.data, status=status.HTTP_200_OK)

            subtotal = sum(item.quantity * item.price for item in items)
            tax_percent = Decimal(str(request.data.get('tax_percent', str(old_bill.tax_percent))))
            discount = Decimal(str(request.data.get('discount', str(old_bill.discount))))
            payment_mode = request.data.get('payment_mode', old_bill.payment_mode)
            amount_received = Decimal(str(request.data.get('amount_received', str(old_bill.amount_received))))

            tax_amount = (subtotal * tax_percent / Decimal('100')).quantize(Decimal('0.01'))
            total_amount = (subtotal + tax_amount - discount).quantize(Decimal('0.01'))
            change_amount = max(Decimal('0'), amount_received - total_amount)

            old_bill.subtotal = subtotal
            old_bill.tax_percent = tax_percent
            old_bill.tax_amount = tax_amount
            old_bill.discount = discount
            old_bill.total_amount = total_amount
            old_bill.payment_mode = payment_mode
            old_bill.amount_received = amount_received
            old_bill.change_amount = change_amount
            old_bill.is_paid = amount_received >= total_amount
            old_bill.save()

            new_status = Order.Status.PAID if old_bill.is_paid else Order.Status.BILLED
            order.status = new_status
            order.save(update_fields=['status', 'updated_at'])

            serializer = BillSerializer(old_bill)
            return Response(serializer.data, status=status.HTTP_200_OK)

        items = order.items.all()
        if not items.exists():
            return Response(
                {'detail': 'Order has no items.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        subtotal = sum(item.quantity * item.price for item in items)
        tax_percent = Decimal(str(request.data.get('tax_percent', '0')))
        discount = Decimal(str(request.data.get('discount', '0')))
        payment_mode = request.data.get('payment_mode', Bill.PaymentMode.CASH)
        amount_received = Decimal(str(request.data.get('amount_received', '0')))

        tax_amount = (subtotal * tax_percent / Decimal('100')).quantize(Decimal('0.01'))
        total_amount = (subtotal + tax_amount - discount).quantize(Decimal('0.01'))
        change_amount = max(Decimal('0'), amount_received - total_amount)

        bill = Bill.objects.create(
            order=order,
            subtotal=subtotal,
            tax_percent=tax_percent,
            tax_amount=tax_amount,
            discount=discount,
            total_amount=total_amount,
            payment_mode=payment_mode,
            amount_received=amount_received,
            change_amount=change_amount,
            is_paid=amount_received >= total_amount,
        )

        # Advance order status
        new_status = Order.Status.PAID if bill.is_paid else Order.Status.BILLED
        order.status = new_status
        order.save(update_fields=['status', 'updated_at'])

        serializer = BillSerializer(bill)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Custom action: active_orders
    # ------------------------------------------------------------------

    @action(detail=False, methods=['get'], url_path='active_orders')
    def active_orders(self, request):
        """Return all orders that are not yet paid or cancelled."""
        active_statuses = [Order.Status.PENDING, Order.Status.HOLD, Order.Status.KOT]
        qs = (
            Order.objects.filter(status__in=active_statuses)
            .select_related('table', 'customer', 'created_by')
            .prefetch_related('items__menu_item')
            .order_by('-created_at')
        )
        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = OrderReadSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = OrderReadSerializer(qs, many=True)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Bill
# ---------------------------------------------------------------------------

class BillViewSet(viewsets.ModelViewSet):
    """
    List, retrieve, and update bills.
    Create is handled via Order.generate_bill action.
    """

    queryset = (
        Bill.objects.select_related('order__table', 'order__customer')
        .prefetch_related('order__items__menu_item')
        .all()
    )
    serializer_class = BillSerializer
    http_method_names = ['get', 'put', 'patch', 'head', 'options']
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['bill_number', 'order__table__name', 'payment_mode']
    ordering_fields = ['created_at', 'total_amount']

    def get_queryset(self):
        qs = super().get_queryset()
        is_paid = self.request.query_params.get('is_paid')
        if is_paid is not None:
            qs = qs.filter(is_paid=is_paid.lower() in ('true', '1', 'yes'))
        
        # Filter by date
        date_param = self.request.query_params.get('date')
        if date_param == 'today':
            from django.utils import timezone
            today = timezone.now().date()
            qs = qs.filter(created_at__date=today)
        
        return qs

    @action(detail=False, methods=['get'], url_path='today_report')
    def today_report(self, request):
        """
        GET /api/bills/today_report/
        Returns today's sales summary with payment mode breakdown.
        """
        from django.utils import timezone
        from django.db.models import Sum, Count, Q
        
        today = timezone.now().date()
        today_bills = Bill.objects.filter(created_at__date=today)
        
        # Total sales
        total_sales = today_bills.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        total_count = today_bills.count()
        
        # Payment mode breakdown
        payment_breakdown = []
        for mode_key, mode_label in Bill.PaymentMode.choices:
            mode_bills = today_bills.filter(payment_mode=mode_key)
            mode_total = mode_bills.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
            mode_count = mode_bills.count()
            if mode_count > 0:
                payment_breakdown.append({
                    'mode': mode_key,
                    'label': mode_label,
                    'count': mode_count,
                    'total': str(mode_total),
                })
        
        # Recent bills (last 20)
        recent_bills = today_bills.order_by('-created_at')[:20]
        bills_data = BillSerializer(recent_bills, many=True).data
        
        return Response({
            'date': str(today),
            'total_sales': str(total_sales),
            'total_bills': total_count,
            'payment_breakdown': payment_breakdown,
            'recent_bills': bills_data,
        })

    def update(self, request, *args, **kwargs):
        """Allow updating payment details (mark as paid, update payment mode, etc.)."""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        bill = serializer.save()

        # Sync order status when bill is marked paid
        if bill.is_paid and bill.order.status != Order.Status.PAID:
            bill.order.status = Order.Status.PAID
            bill.order.save(update_fields=['status', 'updated_at'])

        return Response(serializer.data)


# ---------------------------------------------------------------------------
# KOT
# ---------------------------------------------------------------------------

class KOTViewSet(viewsets.ModelViewSet):
    """List, retrieve, and mark KOTs as printed."""

    queryset = KOT.objects.select_related('order').all()
    serializer_class = KOTSerializer
    http_method_names = ['get', 'patch', 'head', 'options']
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['kot_number', 'order__id']
    ordering_fields = ['created_at']

    def get_queryset(self):
        qs = super().get_queryset()
        order_id = self.request.query_params.get('order')
        if order_id:
            qs = qs.filter(order_id=order_id)
        printed = self.request.query_params.get('printed')
        if printed is not None:
            qs = qs.filter(printed=printed.lower() in ('true', '1', 'yes'))
        return qs

    @action(detail=True, methods=['patch'], url_path='mark_printed')
    def mark_printed(self, request, pk=None):
        """Mark a KOT as printed."""
        kot = self.get_object()
        kot.printed = True
        kot.save(update_fields=['printed'])
        serializer = self.get_serializer(kot)
        return Response(serializer.data)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardView(APIView):
    """
    GET /api/dashboard/
    Returns today's summary: sales total, bill count, active tables, recent orders.
    """

    def get(self, request):
        today = timezone.now().date()

        # Today's paid bills
        today_bills = Bill.objects.filter(
            is_paid=True,
            created_at__date=today,
        )
        today_sales = today_bills.aggregate(total=Sum('total_amount'))['total'] or Decimal('0')
        today_bill_count = today_bills.count()

        # Active tables (currently occupied)
        active_tables_count = Table.objects.filter(is_occupied=True).count()

        # Active orders (pending / hold / KOT)
        active_order_count = Order.objects.filter(
            status__in=[Order.Status.PENDING, Order.Status.HOLD, Order.Status.KOT]
        ).count()

        # Recent 10 orders
        recent_orders = (
            Order.objects.select_related('table', 'customer')
            .prefetch_related('items__menu_item')
            .order_by('-created_at')[:10]
        )
        recent_orders_data = OrderReadSerializer(recent_orders, many=True).data

        # Orders by status breakdown
        status_breakdown = (
            Order.objects.values('status')
            .annotate(count=Count('id'))
            .order_by('status')
        )

        return Response({
            'today_sale': today_sales,
            'today_bills': today_bill_count,
            'active_tables': active_tables_count,
            'active_orders': active_order_count,
            'status_breakdown': list(status_breakdown),
            'recent_orders': recent_orders_data,
        })


# ---------------------------------------------------------------------------
# Clear All Data
# ---------------------------------------------------------------------------

class ClearDataView(APIView):
    """
    POST /api/clear-data/
    Deletes all orders, bills, KOTs and resets table occupancy.
    Categories, menu items and tables are kept.
    """

    def post(self, request):
        # Delete in correct order (FK constraints)
        KOT.objects.all().delete()
        Bill.objects.all().delete()
        from django.db import connection
        # Delete order items then orders
        from api.models import OrderItem
        OrderItem.objects.all().delete()
        Order.objects.all().delete()

        # Reset all tables to available
        Table.objects.all().update(is_occupied=False)

        return Response({
            'detail': 'All orders, bills and KOTs have been cleared. Tables reset to available.'
        })

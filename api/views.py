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
    http_method_names = ['get', 'put', 'patch', 'delete', 'head', 'options']
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

    def destroy(self, request, *args, **kwargs):
        """Delete a bill and reset the associated order status to PENDING."""
        bill = self.get_object()
        order = bill.order
        bill.delete()
        # Reset order so it can be re-billed
        order.status = Order.Status.PENDING
        order.save(update_fields=['status', 'updated_at'])
        return Response({'detail': 'Bill deleted. Order reset to pending.'}, status=status.HTTP_200_OK)


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

        # Today's bills (both BILLED and PAID)
        today_bills = Bill.objects.filter(
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
# Item Sales Report
# ---------------------------------------------------------------------------

class ItemSalesReportView(APIView):
    """
    GET /api/reports/item-sales/?date=today|YYYY-MM-DD&month=YYYY-MM
    Item-wise sales — quantity sold and total amount per item.
    """

    def get(self, request):
        from django.db.models import Sum, F
        import datetime

        month_param = request.query_params.get('month')
        date_param  = request.query_params.get('date', 'today')

        if month_param:
            # Monthly item report
            try:
                year, month = map(int, month_param.split('-'))
            except Exception:
                today = timezone.now().date()
                year, month = today.year, today.month
            paid_orders = Order.objects.filter(
                status__in=[Order.Status.PAID, Order.Status.BILLED],
                updated_at__year=year,
                updated_at__month=month,
            )
            label = f'{datetime.date(year, month, 1).strftime("%B %Y")}'
        else:
            if date_param == 'today':
                target_date = timezone.now().date()
            else:
                try:
                    target_date = datetime.date.fromisoformat(date_param)
                except ValueError:
                    target_date = timezone.now().date()
            paid_orders = Order.objects.filter(
                status__in=[Order.Status.PAID, Order.Status.BILLED],
                updated_at__date=target_date,
            )
            label = str(target_date)

        item_sales = (
            OrderItem.objects.filter(order__in=paid_orders)
            .values('menu_item__id', 'menu_item__name', 'menu_item__category__name')
            .annotate(
                total_qty=Sum('quantity'),
                total_amount=Sum(F('quantity') * F('price')),
            )
            .order_by('-total_qty')
        )

        result = []
        grand_qty    = 0
        grand_amount = Decimal('0')
        for row in item_sales:
            qty    = row['total_qty'] or 0
            amount = row['total_amount'] or Decimal('0')
            grand_qty    += qty
            grand_amount += amount
            result.append({
                'item_id':      row['menu_item__id'],
                'item_name':    row['menu_item__name'],
                'category':     row['menu_item__category__name'] or 'Uncategorized',
                'quantity':     qty,
                'total_amount': str(amount.quantize(Decimal('0.01'))),
            })

        return Response({
            'period':              label,
            'items':               result,
            'grand_total_qty':     grand_qty,
            'grand_total_amount':  str(grand_amount.quantize(Decimal('0.01'))),
        })


# ---------------------------------------------------------------------------
# Daily Sales Report
# ---------------------------------------------------------------------------

class DailySalesReportView(APIView):
    """
    GET /api/reports/daily/?date=YYYY-MM-DD  (default: today)
    Full daily sales breakdown.
    """

    def get(self, request):
        import datetime
        date_param = request.query_params.get('date', 'today')
        if date_param == 'today':
            target_date = timezone.now().date()
        else:
            try:
                target_date = datetime.date.fromisoformat(date_param)
            except ValueError:
                target_date = timezone.now().date()

        bills = Bill.objects.filter(created_at__date=target_date).select_related('order__table')

        total_sales    = bills.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
        total_discount = bills.aggregate(t=Sum('discount'))['t']     or Decimal('0')
        total_tax      = bills.aggregate(t=Sum('tax_amount'))['t']   or Decimal('0')
        total_bills    = bills.count()

        payment_breakdown = []
        for mode_key, mode_label in Bill.PaymentMode.choices:
            mb = bills.filter(payment_mode=mode_key)
            mt = mb.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
            mc = mb.count()
            if mc > 0:
                payment_breakdown.append({
                    'mode': mode_key, 'label': mode_label,
                    'count': mc, 'total': str(mt.quantize(Decimal('0.01'))),
                })

        from .serializers import BillSerializer
        bills_data = BillSerializer(bills.order_by('-created_at'), many=True).data

        return Response({
            'date':              str(target_date),
            'total_sales':       str(total_sales.quantize(Decimal('0.01'))),
            'total_bills':       total_bills,
            'total_discount':    str(total_discount.quantize(Decimal('0.01'))),
            'total_tax':         str(total_tax.quantize(Decimal('0.01'))),
            'payment_breakdown': payment_breakdown,
            'bills':             bills_data,
        })


# ---------------------------------------------------------------------------
# Monthly Sales Report
# ---------------------------------------------------------------------------

class MonthlySalesReportView(APIView):
    """
    GET /api/reports/monthly/?month=YYYY-MM  (default: current month)
    Monthly sales with day-wise breakdown.
    """

    def get(self, request):
        import datetime, calendar
        month_param = request.query_params.get('month')
        today = timezone.now().date()
        if month_param:
            try:
                year, month = map(int, month_param.split('-'))
            except Exception:
                year, month = today.year, today.month
        else:
            year, month = today.year, today.month

        bills = Bill.objects.filter(
            created_at__year=year, created_at__month=month
        ).select_related('order__table')

        from django.db.models.functions import TruncDate

        total_sales    = bills.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
        total_discount = bills.aggregate(t=Sum('discount'))['t']     or Decimal('0')
        total_tax      = bills.aggregate(t=Sum('tax_amount'))['t']   or Decimal('0')
        total_bills    = bills.count()

        # Day-wise breakdown
        daily = (
            bills.annotate(day=TruncDate('created_at'))
            .values('day')
            .annotate(sales=Sum('total_amount'), count=Count('id'))
            .order_by('day')
        )
        daily_data = [
            {
                'date':  str(row['day']),
                'sales': str((row['sales'] or Decimal('0')).quantize(Decimal('0.01'))),
                'bills': row['count'],
            }
            for row in daily
        ]

        # Payment breakdown
        payment_breakdown = []
        for mode_key, mode_label in Bill.PaymentMode.choices:
            mb = bills.filter(payment_mode=mode_key)
            mt = mb.aggregate(t=Sum('total_amount'))['t'] or Decimal('0')
            mc = mb.count()
            if mc > 0:
                payment_breakdown.append({
                    'mode': mode_key, 'label': mode_label,
                    'count': mc, 'total': str(mt.quantize(Decimal('0.01'))),
                })

        month_label = datetime.date(year, month, 1).strftime('%B %Y')
        days_in_month = calendar.monthrange(year, month)[1]

        return Response({
            'month':             month_label,
            'year':              year,
            'month_num':         month,
            'days_in_month':     days_in_month,
            'total_sales':       str(total_sales.quantize(Decimal('0.01'))),
            'total_bills':       total_bills,
            'total_discount':    str(total_discount.quantize(Decimal('0.01'))),
            'total_tax':         str(total_tax.quantize(Decimal('0.01'))),
            'avg_daily_sales':   str((total_sales / days_in_month).quantize(Decimal('0.01'))) if days_in_month else '0',
            'payment_breakdown': payment_breakdown,
            'daily_breakdown':   daily_data,
        })


# ---------------------------------------------------------------------------
# Profit & Loss Report
# ---------------------------------------------------------------------------

class ProfitLossReportView(APIView):
    """
    GET /api/reports/profit-loss/?month=YYYY-MM or ?date=YYYY-MM-DD
    P&L based on selling price vs cost_price of menu items.
    """

    def get(self, request):
        import datetime
        month_param = request.query_params.get('month')
        date_param  = request.query_params.get('date')
        today = timezone.now().date()

        if date_param:
            try:
                target_date = datetime.date.fromisoformat(date_param)
            except ValueError:
                target_date = today
            paid_orders = Order.objects.filter(
                status__in=[Order.Status.PAID, Order.Status.BILLED],
                updated_at__date=target_date,
            )
            period_label = str(target_date)
            period_type  = 'daily'
        elif month_param:
            try:
                year, month = map(int, month_param.split('-'))
            except Exception:
                year, month = today.year, today.month
            paid_orders = Order.objects.filter(
                status__in=[Order.Status.PAID, Order.Status.BILLED],
                updated_at__year=year,
                updated_at__month=month,
            )
            period_label = datetime.date(year, month, 1).strftime('%B %Y')
            period_type  = 'monthly'
        else:
            paid_orders = Order.objects.filter(
                status__in=[Order.Status.PAID, Order.Status.BILLED],
                updated_at__date=today,
            )
            period_label = str(today)
            period_type  = 'daily'

        # Revenue = sum of (qty * selling_price) from order items
        order_items = OrderItem.objects.filter(order__in=paid_orders).select_related('menu_item')

        total_revenue = Decimal('0')
        total_cost    = Decimal('0')
        item_pl       = {}

        for oi in order_items:
            revenue = oi.quantity * oi.price
            cost    = oi.quantity * (oi.menu_item.cost_price or Decimal('0'))
            total_revenue += revenue
            total_cost    += cost

            mid = oi.menu_item.id
            if mid not in item_pl:
                item_pl[mid] = {
                    'item_id':   mid,
                    'item_name': oi.menu_item.name,
                    'qty':       0,
                    'revenue':   Decimal('0'),
                    'cost':      Decimal('0'),
                    'profit':    Decimal('0'),
                }
            item_pl[mid]['qty']     += oi.quantity
            item_pl[mid]['revenue'] += revenue
            item_pl[mid]['cost']    += cost
            item_pl[mid]['profit']  += (revenue - cost)

        # Bills for discount/tax
        bills = Bill.objects.filter(order__in=paid_orders)
        total_discount = bills.aggregate(t=Sum('discount'))['t']   or Decimal('0')
        total_tax      = bills.aggregate(t=Sum('tax_amount'))['t'] or Decimal('0')

        gross_profit = total_revenue - total_cost
        net_profit   = gross_profit - total_discount  # tax is already in revenue

        item_list = sorted(item_pl.values(), key=lambda x: x['profit'], reverse=True)
        for i in item_list:
            i['revenue'] = str(i['revenue'].quantize(Decimal('0.01')))
            i['cost']    = str(i['cost'].quantize(Decimal('0.01')))
            i['profit']  = str(i['profit'].quantize(Decimal('0.01')))

        margin = (gross_profit / total_revenue * 100) if total_revenue > 0 else Decimal('0')

        return Response({
            'period':         period_label,
            'period_type':    period_type,
            'total_revenue':  str(total_revenue.quantize(Decimal('0.01'))),
            'total_cost':     str(total_cost.quantize(Decimal('0.01'))),
            'total_discount': str(total_discount.quantize(Decimal('0.01'))),
            'total_tax':      str(total_tax.quantize(Decimal('0.01'))),
            'gross_profit':   str(gross_profit.quantize(Decimal('0.01'))),
            'net_profit':     str(net_profit.quantize(Decimal('0.01'))),
            'profit_margin':  str(margin.quantize(Decimal('0.01'))),
            'items':          item_list,
        })


# ---------------------------------------------------------------------------
# Item Sales Report (legacy — kept for backward compat)
# ---------------------------------------------------------------------------

class ItemSalesReportView(APIView):
    """
    GET /api/reports/item-sales/?date=today
    Returns today's item-wise sales report — quantity sold and total amount per item.
    """

    def get(self, request):
        from django.utils import timezone
        from django.db.models import Sum, F

        date_param = request.query_params.get('date', 'today')
        if date_param == 'today':
            target_date = timezone.now().date()
        else:
            try:
                from datetime import date
                target_date = date.fromisoformat(date_param)
            except ValueError:
                target_date = timezone.now().date()

        # Only count items from PAID or BILLED orders
        paid_orders = Order.objects.filter(
            status__in=[Order.Status.PAID, Order.Status.BILLED],
            updated_at__date=target_date,
        )

        # Aggregate by menu item
        item_sales = (
            OrderItem.objects.filter(order__in=paid_orders)
            .values(
                'menu_item__id',
                'menu_item__name',
                'menu_item__category__name',
            )
            .annotate(
                total_qty=Sum('quantity'),
                total_amount=Sum(F('quantity') * F('price')),
            )
            .order_by('-total_qty')
        )

        result = []
        grand_qty = 0
        grand_amount = Decimal('0')

        for row in item_sales:
            qty = row['total_qty'] or 0
            amount = row['total_amount'] or Decimal('0')
            grand_qty += qty
            grand_amount += amount
            result.append({
                'item_id': row['menu_item__id'],
                'item_name': row['menu_item__name'],
                'category': row['menu_item__category__name'] or 'Uncategorized',
                'quantity': qty,
                'total_amount': str(amount.quantize(Decimal('0.01'))),
            })

        return Response({
            'date': str(target_date),
            'items': result,
            'grand_total_qty': grand_qty,
            'grand_total_amount': str(grand_amount.quantize(Decimal('0.01'))),
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

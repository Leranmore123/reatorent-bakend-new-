"""Serializers for the Restaurant Billing API."""

from decimal import Decimal
from rest_framework import serializers
from .models import Category, MenuItem, Table, Customer, Order, OrderItem, Bill, KOT


# ---------------------------------------------------------------------------
# Category
# ---------------------------------------------------------------------------

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ['id', 'name', 'created_at']
        read_only_fields = ['id', 'created_at']


# ---------------------------------------------------------------------------
# MenuItem
# ---------------------------------------------------------------------------

class MenuItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = MenuItem
        fields = ['id', 'category', 'category_name', 'name', 'price', 'cost_price', 'is_available', 'created_at']
        read_only_fields = ['id', 'created_at']


class MenuItemBriefSerializer(serializers.ModelSerializer):
    """Lightweight serializer used inside OrderItem."""

    class Meta:
        model = MenuItem
        fields = ['id', 'name', 'price']


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

class TableSerializer(serializers.ModelSerializer):
    active_order_id = serializers.SerializerMethodField()
    active_order_total = serializers.SerializerMethodField()
    active_order_status = serializers.SerializerMethodField()
    active_order_item_count = serializers.SerializerMethodField()

    class Meta:
        model = Table
        fields = ['id', 'name', 'capacity', 'is_occupied',
                  'active_order_id', 'active_order_total',
                  'active_order_status', 'active_order_item_count', 'created_at']
        read_only_fields = ['id', 'is_occupied', 'created_at']

    def _get_active_order(self, obj):
        return obj.orders.filter(
            status__in=[Order.Status.PENDING, Order.Status.HOLD, Order.Status.KOT]
        ).order_by('-created_at').first()

    def get_active_order_id(self, obj):
        order = self._get_active_order(obj)
        return order.id if order else None

    def get_active_order_total(self, obj):
        order = self._get_active_order(obj)
        if not order:
            return None
        total = sum(item.quantity * item.price for item in order.items.all())
        return str(total)

    def get_active_order_status(self, obj):
        order = self._get_active_order(obj)
        return order.status if order else None

    def get_active_order_item_count(self, obj):
        order = self._get_active_order(obj)
        if not order:
            return 0
        return sum(item.quantity for item in order.items.all())


# ---------------------------------------------------------------------------
# Customer
# ---------------------------------------------------------------------------

class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = ['id', 'name', 'phone', 'created_at']
        read_only_fields = ['id', 'created_at']


# ---------------------------------------------------------------------------
# OrderItem
# ---------------------------------------------------------------------------

class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_detail = MenuItemBriefSerializer(source='menu_item', read_only=True)
    line_total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ['id', 'menu_item', 'menu_item_detail', 'quantity', 'price', 'notes', 'line_total']
        read_only_fields = ['id', 'price']

    def validate_menu_item(self, value):
        if not value.is_available:
            raise serializers.ValidationError(f'"{value.name}" is currently not available.')
        return value


class OrderItemWriteSerializer(serializers.ModelSerializer):
    """Used when creating/updating order items; auto-fills price from menu item."""

    class Meta:
        model = OrderItem
        fields = ['id', 'menu_item', 'quantity', 'notes']
        read_only_fields = ['id']


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

class OrderReadSerializer(serializers.ModelSerializer):
    """Full nested read serializer for an order."""

    items = OrderItemSerializer(many=True, read_only=True)
    table_name = serializers.CharField(source='table.name', read_only=True)
    customer_name = serializers.CharField(source='customer.name', read_only=True)
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    order_total = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = [
            'id', 'table', 'table_name', 'customer', 'customer_name',
            'order_type', 'status', 'items', 'order_total',
            'created_by', 'created_by_username', 'created_at', 'updated_at',
        ]

    def get_order_total(self, obj):
        total = sum(item.quantity * item.price for item in obj.items.all())
        return str(total)


class OrderItemInlineSerializer(serializers.Serializer):
    """Used inside OrderWriteSerializer to accept items inline."""

    menu_item = serializers.PrimaryKeyRelatedField(queryset=MenuItem.objects.filter(is_available=True))
    quantity = serializers.IntegerField(min_value=1)
    notes = serializers.CharField(required=False, allow_blank=True, default='')


class OrderWriteSerializer(serializers.ModelSerializer):
    """Handles create/update of an order, including inline items."""

    items = OrderItemInlineSerializer(many=True, required=False)

    class Meta:
        model = Order
        fields = ['id', 'table', 'customer', 'order_type', 'status', 'items', 'created_by']
        read_only_fields = ['id']

    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        order = Order.objects.create(**validated_data)
        for item_data in items_data:
            menu_item = item_data['menu_item']
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=item_data['quantity'],
                price=menu_item.price,
                notes=item_data.get('notes', ''),
            )
        return order

    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            # Replace all items with the new set
            instance.items.all().delete()
            for item_data in items_data:
                menu_item = item_data['menu_item']
                OrderItem.objects.create(
                    order=instance,
                    menu_item=menu_item,
                    quantity=item_data['quantity'],
                    price=menu_item.price,
                    notes=item_data.get('notes', ''),
                )
        return instance


# ---------------------------------------------------------------------------
# Bill
# ---------------------------------------------------------------------------

class BillSerializer(serializers.ModelSerializer):
    order_detail = OrderReadSerializer(source='order', read_only=True)

    class Meta:
        model = Bill
        fields = [
            'id', 'order', 'order_detail', 'bill_number',
            'subtotal', 'tax_percent', 'tax_amount', 'discount', 'total_amount',
            'payment_mode', 'amount_received', 'change_amount', 'is_paid', 'created_at',
        ]
        read_only_fields = ['id', 'bill_number', 'subtotal', 'tax_amount', 'total_amount', 'created_at']

    def validate(self, data):
        amount_received = data.get('amount_received', Decimal('0'))
        total_amount = self.instance.total_amount if self.instance else data.get('total_amount', Decimal('0'))
        if data.get('is_paid') and amount_received < total_amount:
            raise serializers.ValidationError(
                'Amount received cannot be less than total amount when marking as paid.'
            )
        return data


class BillCreateSerializer(serializers.ModelSerializer):
    """Used internally when generating a bill from an order."""

    class Meta:
        model = Bill
        fields = [
            'id', 'order', 'bill_number',
            'subtotal', 'tax_percent', 'tax_amount', 'discount', 'total_amount',
            'payment_mode', 'amount_received', 'change_amount', 'is_paid', 'created_at',
        ]
        read_only_fields = ['id', 'bill_number', 'subtotal', 'tax_amount', 'total_amount', 'created_at']


# ---------------------------------------------------------------------------
# KOT
# ---------------------------------------------------------------------------

class KOTSerializer(serializers.ModelSerializer):
    table_name = serializers.SerializerMethodField()

    class Meta:
        model = KOT
        fields = ['id', 'order', 'kot_number', 'items_snapshot',
                  'table_name', 'created_at', 'printed']
        read_only_fields = ['id', 'kot_number', 'items_snapshot', 'created_at']

    def get_table_name(self, obj):
        if obj.order and obj.order.table:
            return obj.order.table.name
        if obj.order and obj.order.order_type == 'TAKEAWAY':
            return 'Take Away'
        return 'Take Away'

"""Django admin configuration for the Restaurant Billing app."""

from django.contrib import admin
from .models import Category, MenuItem, Table, Customer, Order, OrderItem, Bill, KOT


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'created_at']
    search_fields = ['name']


@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'category', 'price', 'is_available', 'created_at']
    list_filter = ['category', 'is_available']
    search_fields = ['name']
    list_editable = ['price', 'is_available']


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'capacity', 'is_occupied', 'created_at']
    list_filter = ['is_occupied']
    search_fields = ['name']


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'phone', 'created_at']
    search_fields = ['name', 'phone']


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['price']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'order_type', 'status', 'table', 'customer', 'created_by', 'created_at']
    list_filter = ['status', 'order_type']
    search_fields = ['table__name', 'customer__name']
    inlines = [OrderItemInline]
    readonly_fields = ['created_at', 'updated_at']


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'menu_item', 'quantity', 'price', 'notes']
    search_fields = ['order__id', 'menu_item__name']


@admin.register(Bill)
class BillAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'bill_number', 'order', 'subtotal', 'tax_amount',
        'discount', 'total_amount', 'payment_mode', 'is_paid', 'created_at',
    ]
    list_filter = ['is_paid', 'payment_mode']
    search_fields = ['bill_number', 'order__id']
    readonly_fields = ['bill_number', 'created_at']


@admin.register(KOT)
class KOTAdmin(admin.ModelAdmin):
    list_display = ['id', 'kot_number', 'order', 'printed', 'created_at']
    list_filter = ['printed']
    search_fields = ['kot_number', 'order__id']
    readonly_fields = ['kot_number', 'items_snapshot', 'created_at']

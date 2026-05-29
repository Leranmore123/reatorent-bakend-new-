"""Models for the Restaurant Billing System."""

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class Category(models.Model):
    """Food/drink category (e.g. Starters, Mains, Beverages)."""

    name = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = 'Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    """An item on the restaurant menu."""

    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, related_name='menu_items'
    )
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    cost_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['category', 'name']

    def __str__(self):
        return f'{self.name} (₹{self.price})'


class Table(models.Model):
    """A physical table in the restaurant."""

    name = models.CharField(max_length=50, unique=True)
    capacity = models.PositiveIntegerField(default=4)
    is_occupied = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Customer(models.Model):
    """A customer record (used for takeaway/delivery or loyalty tracking)."""

    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} ({self.phone or "no phone"})'


class Order(models.Model):
    """A customer order — dine-in, takeaway, or delivery."""

    class OrderType(models.TextChoices):
        DINE_IN = 'DINE_IN', 'Dine In'
        TAKEAWAY = 'TAKEAWAY', 'Takeaway'
        DELIVERY = 'DELIVERY', 'Delivery'

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        HOLD = 'HOLD', 'On Hold'
        KOT = 'KOT', 'KOT Generated'
        BILLED = 'BILLED', 'Billed'
        PAID = 'PAID', 'Paid'
        CANCELLED = 'CANCELLED', 'Cancelled'

    table = models.ForeignKey(
        Table, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders'
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders'
    )
    order_type = models.CharField(
        max_length=20, choices=OrderType.choices, default=OrderType.DINE_IN
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='orders'
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order #{self.pk} — {self.order_type} [{self.status}]'

    def save(self, *args, **kwargs):
        # Mark table as occupied when a dine-in order is active
        if self.table:
            if self.status in (self.Status.PENDING, self.Status.HOLD, self.Status.KOT):
                # Active orders — table occupied
                self.table.is_occupied = True
            else:
                # BILLED, PAID, CANCELLED — check if any other active orders exist
                active = Order.objects.filter(
                    table=self.table,
                    status__in=[self.Status.PENDING, self.Status.HOLD, self.Status.KOT],
                ).exclude(pk=self.pk)
                self.table.is_occupied = active.exists()
            self.table.save(update_fields=['is_occupied'])
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    """A single line item within an order."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    menu_item = models.ForeignKey(
        MenuItem, on_delete=models.PROTECT, related_name='order_items'
    )
    quantity = models.PositiveIntegerField(default=1)
    # Price captured at the time of ordering so historical bills stay accurate
    price = models.DecimalField(max_digits=10, decimal_places=2)
    notes = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.quantity}x {self.menu_item.name} @ ₹{self.price}'

    @property
    def line_total(self):
        return self.quantity * self.price


class Bill(models.Model):
    """The bill generated for a completed order."""

    class PaymentMode(models.TextChoices):
        CASH = 'CASH', 'Cash'
        UPI = 'UPI', 'UPI'
        CARD = 'CARD', 'Card'
        CHEQUE = 'CHEQUE', 'Cheque'

    order = models.OneToOneField(Order, on_delete=models.PROTECT, related_name='bill')
    bill_number = models.CharField(max_length=20, unique=True, blank=True)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    payment_mode = models.CharField(
        max_length=10, choices=PaymentMode.choices, default=PaymentMode.CASH
    )
    amount_received = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    change_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    is_paid = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.bill_number

    def _generate_bill_number(self):
        last = Bill.objects.order_by('-id').first()
        next_id = (last.id + 1) if last else 1
        return f'BILL-{next_id:04d}'

    def save(self, *args, **kwargs):
        if not self.bill_number:
            self.bill_number = self._generate_bill_number()
        super().save(*args, **kwargs)


class KOT(models.Model):
    """Kitchen Order Ticket — a snapshot of items sent to the kitchen."""

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='kots')
    kot_number = models.CharField(max_length=20, unique=True, blank=True)
    # Snapshot of items so the KOT is immutable even if the order changes later
    items_snapshot = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    printed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'KOT'
        verbose_name_plural = 'KOTs'

    def __str__(self):
        return self.kot_number

    def _generate_kot_number(self):
        last = KOT.objects.order_by('-id').first()
        next_id = (last.id + 1) if last else 1
        return f'KOT-{next_id:04d}'

    def save(self, *args, **kwargs):
        if not self.kot_number:
            self.kot_number = self._generate_kot_number()
        super().save(*args, **kwargs)

import os, django
os.environ['DJANGO_SETTINGS_MODULE'] = 'restaurant_project.settings'
django.setup()
from api.models import Order, Table

print("Fixing occupied tables with BILLED/PAID/CANCELLED orders...")
for table in Table.objects.all():
    active = Order.objects.filter(
        table=table,
        status__in=['PENDING', 'HOLD', 'KOT']
    ).first()
    should_be_occupied = active is not None
    if table.is_occupied != should_be_occupied:
        table.is_occupied = should_be_occupied
        table.save(update_fields=['is_occupied'])
        print(f"  Fixed: {table.name} -> occupied={should_be_occupied}")

print("\nCurrent table status:")
for table in Table.objects.all():
    active = Order.objects.filter(table=table, status__in=['PENDING','HOLD','KOT']).first()
    status_str = f"Order #{active.id} [{active.status}]" if active else "No active order"
    print(f"  {table.name}: occupied={table.is_occupied} | {status_str}")

"""
Sample data loader for Restaurant Billing System.
Run: python load_sample_data.py
"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'restaurant_project.settings')
django.setup()

from api.models import Category, MenuItem, Table

# Categories
categories_data = [
    'Starters', 'Chinese', 'Biryani', 'Pasta', 'Beverages',
    'South Indian', 'Dumplings', 'Burger', 'Bakery', 'Daal',
]

print("Creating categories...")
categories = {}
for name in categories_data:
    cat, created = Category.objects.get_or_create(name=name)
    categories[name] = cat
    if created:
        print(f"  + {name}")

# Menu Items
menu_items = [
    # Starters
    ('Paneer Tikka', 'Starters', 150),
    ('Chicken Tikka Half', 'Starters', 180),
    ('Veg Noodles Full', 'Starters', 250),
    # Chinese
    ('Chicken 65', 'Chinese', 180),
    ('Fried Rice', 'Chinese', 120),
    # Biryani
    ('Chicken Biryani Half', 'Biryani', 70),
    ('Hyderabad Chicken Dum', 'Biryani', 220),
    ('Mutton Biryani', 'Biryani', 400),
    ('Seekh Biryani', 'Biryani', 150),
    ('Veg Biryani', 'Biryani', 120),
    # Pasta
    ('Alfredo Pasta', 'Pasta', 170),
    ('Makhani Sauce Pasta', 'Pasta', 180),
    # Beverages
    ('Coffee', 'Beverages', 30),
    ('Cold Coffee', 'Beverages', 105),
    ('Tea', 'Beverages', 10),
    # South Indian
    ('Masala Dosa', 'South Indian', 100),
    ('Mendu Vada', 'South Indian', 40),
    ('Idli', 'South Indian', 50),
    ('Samosa', 'South Indian', 15),
    # Dumplings
    ('Mushroom Dumplings', 'Dumplings', 140),
    ('Spinach Dumplings', 'Dumplings', 150),
    ('Veggies Dumplings', 'Dumplings', 120),
    # Burger
    ('Burger', 'Burger', 90),
    # Bakery
    ('Butter Khari', 'Bakery', 80),
    ('Pattice', 'Bakery', 30),
    ('Pizza', 'Bakery', 50),
    # Daal
    ('Daal Makhni', 'Daal', 200),
    ('Taal Tadka', 'Daal', 150),
]

print("\nCreating menu items...")
for name, cat_name, price in menu_items:
    item, created = MenuItem.objects.get_or_create(
        name=name,
        defaults={'category': categories[cat_name], 'price': price}
    )
    if created:
        print(f"  + {name} (₹{price})")

# Tables
print("\nCreating tables...")
for i in range(1, 11):
    table, created = Table.objects.get_or_create(
        name=f'Table {i}',
        defaults={'capacity': 4}
    )
    if created:
        print(f"  + Table {i}")

# Take Away
table, created = Table.objects.get_or_create(
    name='Take Away',
    defaults={'capacity': 0}
)
if created:
    print("  + Take Away")

print("\n✅ Sample data loaded successfully!")
print(f"   Categories: {Category.objects.count()}")
print(f"   Menu Items: {MenuItem.objects.count()}")
print(f"   Tables: {Table.objects.count()}")

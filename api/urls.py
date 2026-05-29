"""URL configuration for the api app."""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    CategoryViewSet,
    MenuItemViewSet,
    TableViewSet,
    CustomerViewSet,
    OrderViewSet,
    BillViewSet,
    KOTViewSet,
    DashboardView,
    ClearDataView,
    ItemSalesReportView,
    DailySalesReportView,
    MonthlySalesReportView,
    ProfitLossReportView,
)

router = DefaultRouter()
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'menu-items', MenuItemViewSet, basename='menuitem')
router.register(r'tables', TableViewSet, basename='table')
router.register(r'customers', CustomerViewSet, basename='customer')
router.register(r'orders', OrderViewSet, basename='order')
router.register(r'bills', BillViewSet, basename='bill')
router.register(r'kots', KOTViewSet, basename='kot')

urlpatterns = [
    path('', include(router.urls)),
    path('dashboard/', DashboardView.as_view(), name='dashboard'),
    path('clear-data/', ClearDataView.as_view(), name='clear-data'),
    path('reports/item-sales/', ItemSalesReportView.as_view(), name='item-sales-report'),
    path('reports/daily/', DailySalesReportView.as_view(), name='daily-report'),
    path('reports/monthly/', MonthlySalesReportView.as_view(), name='monthly-report'),
    path('reports/profit-loss/', ProfitLossReportView.as_view(), name='profit-loss'),
]

from django.urls import path

from .views import CustomerCreateView, CustomerDetailView, CustomerListView, VehicleCreateView, VehicleUpdateView

app_name = 'customers'

urlpatterns = [
    path('', CustomerListView.as_view(), name='customer_list'),
    path('novo/', CustomerCreateView.as_view(), name='customer_create'),
    path('<int:pk>/', CustomerDetailView.as_view(), name='customer_detail'),
    path('<int:customer_id>/veiculos/novo/', VehicleCreateView.as_view(), name='vehicle_create'),
    path('veiculos/<int:pk>/editar/', VehicleUpdateView.as_view(), name='vehicle_update'),
]

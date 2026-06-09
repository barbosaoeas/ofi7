from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView

from core.views import RoleRequiredMixin
from users.models import CustomUser

from .models import Customer, Vehicle


class CustomerListView(LoginRequiredMixin, RoleRequiredMixin, ListView):
    model = Customer
    template_name = 'customers/customer_list.html'
    context_object_name = 'customers'
    paginate_by = 25
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)


class CustomerCreateView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    model = Customer
    template_name = 'customers/customer_form.html'
    fields = ('name', 'document_cpf_cnpj', 'phone', 'email')
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get_success_url(self):
        return reverse('customers:customer_detail', kwargs={'pk': self.object.pk})


class CustomerDetailView(LoginRequiredMixin, RoleRequiredMixin, DetailView):
    model = Customer
    template_name = 'customers/customer_detail.html'
    context_object_name = 'customer'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)


class VehicleCreateView(LoginRequiredMixin, RoleRequiredMixin, CreateView):
    model = Vehicle
    template_name = 'customers/vehicle_form.html'
    fields = ('plate', 'brand', 'model', 'color', 'year', 'image_file', 'image_url')
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def dispatch(self, request, *args, **kwargs):
        self.customer = get_object_or_404(Customer, pk=kwargs['customer_id'])
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.instance.customer = self.customer
        return super().form_valid(form)

    def get_success_url(self):
        return reverse('customers:customer_detail', kwargs={'pk': self.customer.pk})

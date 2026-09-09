import hashlib
import hmac
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views import View
from django.views.generic import TemplateView

from users.models import Collaborator, CustomUser, TimeClockDay
from .models import SystemSettings


class RoleRequiredMixin(LoginRequiredMixin):
    """Mixin que SEMPRE exige login e, se allowed_roles for definido, exige a role.

    Herda de LoginRequiredMixin para garantir autenticação SEMPRE (mesmo que
    allowed_roles seja None ou alguém inverta a ordem na classe filha).
    Se allowed_roles for None: permite QUALQUER usuário autenticado.
    Se allowed_roles for setado: exige superuser OU uma das roles listadas.
    Usuários VISUAL logados mas sem permissão caem no kanban de hoje.
    Usuários não logados são redirecionados para a tela de login (via LoginRequiredMixin).
    """
    allowed_roles = None

    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if getattr(response, 'status_code', None) == 302:
            return response

        roles = self.allowed_roles
        if roles is None:
            return response

        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            if getattr(user, 'is_superuser', False):
                return response
            if getattr(user, 'role', None) in roles:
                return response

        messages.error(request, 'Sem permissão para acessar esta página.')
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.VISUAL
        ):
            return redirect('core:timeclock_visual')
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.PONTO
        ):
            return redirect('core:timeclock_ponto')
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.TVKANBAN
        ):
            return redirect('budgets:kanban_today')
        return redirect('core:dashboard')


class PublicIndexView(TemplateView):
    template_name = 'core/public_index.html'

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            return redirect('core:dashboard')
        return super().dispatch(request, *args, **kwargs)


class DashboardView(RoleRequiredMixin, TemplateView):
    template_name = 'core/dashboard.html'

    def dispatch(self, request, *args, **kwargs):
        user = getattr(request, 'user', None)
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.VISUAL
        ):
            return redirect('core:timeclock_visual')
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.PONTO
        ):
            return redirect('core:timeclock_ponto')
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not getattr(user, 'is_superuser', False)
            and getattr(user, 'role', None) == CustomUser.Role.TVKANBAN
        ):
            return redirect('budgets:kanban_today')
        return super().dispatch(request, *args, **kwargs)


class SystemSettingsView(RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get(self, request):
        settings_obj = SystemSettings.get_solo()
        return render(request, 'core/system_settings.html', {'settings': settings_obj})

    def post(self, request):
        settings_obj = SystemSettings.get_solo()

        settings_obj.name = (request.POST.get('name') or '').strip()
        settings_obj.address = (request.POST.get('address') or '').strip()
        settings_obj.phone = (request.POST.get('phone') or '').strip()
        settings_obj.primary_color = (request.POST.get('primary_color') or '').strip()
        settings_obj.secondary_color = (request.POST.get('secondary_color') or '').strip()

        logo = request.FILES.get('logo')
        if logo:
            settings_obj.logo = logo

        if not settings_obj.name:
            settings_obj.name = 'Controle Oficina'
        if not settings_obj.primary_color:
            settings_obj.primary_color = '#D4AF37'
        if not settings_obj.secondary_color:
            settings_obj.secondary_color = '#AA882C'

        settings_obj.updated_at = timezone.now()
        settings_obj.save()
        messages.success(request, 'Configuração salva.')
        return redirect('core:system_settings')


def _timeclock_interval_seconds():
    return 15


def _timeclock_expected_start_time():
    return time(8, 0)


def _timeclock_expected_end_time():
    return time(17, 48)


def _timeclock_earliest_entry_time():
    return time(7, 30)


def _timeclock_extra_grace_time():
    return time(18, 10)


def _timeclock_scheduled_dt(day, t):
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(day, t), tz)


def _timeclock_minutes_between(start_dt, end_dt):
    return int((end_dt - start_dt).total_seconds() // 60)


def _timeclock_bucket(now=None):
    interval = _timeclock_interval_seconds()
    ts = int((now or timezone.now()).timestamp())
    return (ts // interval) * interval


def _timeclock_signature(bucket):
    return hmac.new(settings.SECRET_KEY.encode('utf-8'), str(bucket).encode('utf-8'), hashlib.sha256).hexdigest()


def _timeclock_token(bucket):
    sig = _timeclock_signature(bucket)[:16]
    return f'{bucket}.{sig}'


def _timeclock_code(bucket):
    sig = _timeclock_signature(bucket)
    return str(int(sig[:8], 16) % 1000000).zfill(6)


def _timeclock_validate_token(token):
    raw = (token or '').strip()
    if not raw or '.' not in raw:
        return None
    parts = raw.split('.', 1)
    try:
        bucket = int(parts[0])
    except ValueError:
        return None
    expected = _timeclock_token(bucket)
    if not constant_time_compare(expected, raw):
        return None
    now_bucket = _timeclock_bucket()
    interval = _timeclock_interval_seconds()
    if abs(now_bucket - bucket) > interval:
        return None
    return bucket


def _timeclock_validate_code(code):
    raw = (code or '').strip()
    if not raw or not raw.isdigit():
        return None
    now_bucket = _timeclock_bucket()
    interval = _timeclock_interval_seconds()
    for candidate in (now_bucket, now_bucket - interval):
        if constant_time_compare(_timeclock_code(candidate), raw):
            return candidate
    return None


def _get_eligible_collaborator_for_user(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    email = (getattr(user, 'email', '') or '').strip()
    if not email:
        return None
    return Collaborator.objects.filter(
        email__iexact=email,
        is_active=True,
        function__in=(Collaborator.Function.OPERATIONAL, Collaborator.Function.ESTIMATOR),
    ).first()


def _get_eligible_collaborator_for_ponto_request(request):
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return None
    if getattr(user, 'role', None) in (CustomUser.Role.OPERATIONAL, CustomUser.Role.ESTIMATOR):
        return _get_eligible_collaborator_for_user(user)
    if getattr(user, 'role', None) == CustomUser.Role.PONTO:
        raw_id = (request.POST.get('collaborator_id') or request.GET.get('collaborator_id') or '').strip()
        if not raw_id or not raw_id.isdigit():
            return None
        return Collaborator.objects.filter(
            pk=int(raw_id),
            is_active=True,
            function__in=(Collaborator.Function.OPERATIONAL, Collaborator.Function.ESTIMATOR),
        ).first()
    return None


class TimeClockVisualView(RoleRequiredMixin, TemplateView):
    template_name = 'core/timeclock_visual.html'
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.VISUAL)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['token_url'] = reverse('core:timeclock_visual_token')
        interval = _timeclock_interval_seconds()
        context['interval_seconds'] = interval
        bucket = _timeclock_bucket()
        token = _timeclock_token(bucket)
        code = _timeclock_code(bucket)
        register_url = self.request.build_absolute_uri(reverse('core:timeclock_ponto')) + f'?token={token}'
        now_bucket = _timeclock_bucket()
        expires_in = max(interval - (int(timezone.now().timestamp()) - now_bucket), 0)
        context['initial_code'] = code
        context['initial_qr_value'] = register_url
        context['initial_expires_in'] = expires_in
        return context


class TimeClockVisualTokenView(RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE, CustomUser.Role.VISUAL)

    def get(self, request):
        bucket = _timeclock_bucket()
        token = _timeclock_token(bucket)
        code = _timeclock_code(bucket)
        register_url = request.build_absolute_uri(reverse('core:timeclock_ponto')) + f'?token={token}'
        now_bucket = _timeclock_bucket()
        interval = _timeclock_interval_seconds()
        expires_in = max(interval - (int(timezone.now().timestamp()) - now_bucket), 0)
        return JsonResponse(
            {
                'token': token,
                'code': code,
                'qr_value': register_url,
                'interval_seconds': interval,
                'expires_in': expires_in,
            }
        )


class TimeClockRegisterView(RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.OPERATIONAL, CustomUser.Role.ESTIMATOR)

    def get(self, request):
        user = getattr(request, 'user', None)
        collaborator = _get_eligible_collaborator_for_user(user)
        if collaborator is None:
            messages.error(request, 'Seu usuário não está habilitado para registrar ponto.')
            return redirect('core:dashboard')
        return render(
            request,
            'core/timeclock_register.html',
            {
                'next_url': (request.GET.get('next') or '').strip() or reverse('budgets:kanban_today'),
                'token': (request.GET.get('token') or '').strip(),
                'collaborator_name': collaborator.name,
                'interval_seconds': _timeclock_interval_seconds(),
            },
        )

    def post(self, request):
        user = getattr(request, 'user', None)
        collaborator = _get_eligible_collaborator_for_user(user)
        if collaborator is None:
            messages.error(request, 'Seu usuário não está habilitado para registrar ponto.')
            return redirect('core:dashboard')

        token = (request.POST.get('token') or '').strip()
        code = (request.POST.get('code') or '').strip()
        next_url = (request.POST.get('next') or '').strip() or reverse('budgets:kanban_today')

        bucket = _timeclock_validate_token(token)
        if bucket is None:
            bucket = _timeclock_validate_code(code)
        if bucket is None:
            messages.error(request, 'QR Code/código inválido ou expirado.')
            return redirect(f'{request.path}?next={next_url}')

        today = timezone.localdate()
        now = timezone.now()
        now_local = timezone.localtime(now)
        earliest_dt = _timeclock_scheduled_dt(today, _timeclock_earliest_entry_time())
        start_dt = _timeclock_scheduled_dt(today, _timeclock_expected_start_time())
        end_dt = _timeclock_scheduled_dt(today, _timeclock_expected_end_time())
        grace_dt = _timeclock_scheduled_dt(today, _timeclock_extra_grace_time())
        with transaction.atomic():
            day, _ = TimeClockDay.objects.select_for_update().get_or_create(collaborator=collaborator, date=today)
            if day.entry_at is None:
                if now_local < earliest_dt:
                    messages.error(request, 'Registro de entrada liberado a partir de 07:30.')
                    return redirect(f'{request.path}?next={next_url}')
                day.entry_at = now
                day.minutes_late = max(_timeclock_minutes_between(start_dt, now_local), 0)
                day.minutes_extra = 0
                day.minutes_missing = 0
                day.minutes_worked = 0
                day.save(update_fields=['entry_at', 'minutes_late', 'minutes_extra', 'minutes_missing', 'minutes_worked', 'updated_at'])
                messages.success(request, 'Entrada registrada.')
            elif day.exit_at is None:
                day.exit_at = now
                delta = 0
                if day.entry_at:
                    delta = int((day.exit_at - day.entry_at).total_seconds() // 60)
                day.minutes_worked = max(delta, 0)
                if now_local > grace_dt:
                    day.minutes_extra = max(_timeclock_minutes_between(end_dt, now_local), 0)
                    day.minutes_missing = 0
                elif now_local >= end_dt:
                    day.minutes_missing = 0
                    day.minutes_extra = 0
                else:
                    day.minutes_missing = max(_timeclock_minutes_between(now_local, end_dt), 0)
                    day.minutes_extra = 0
                day.save(update_fields=['exit_at', 'minutes_worked', 'minutes_extra', 'minutes_missing', 'updated_at'])
                messages.success(request, 'Saída registrada.')
            else:
                messages.info(request, 'Sua jornada já está concluída hoje.')

        return redirect(next_url)


class TimeClockPontoView(RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.OPERATIONAL, CustomUser.Role.ESTIMATOR, CustomUser.Role.PONTO)

    def get(self, request):
        user = getattr(request, 'user', None)
        collaborator = _get_eligible_collaborator_for_ponto_request(request)
        if collaborator is None:
            if getattr(user, 'role', None) == CustomUser.Role.PONTO:
                collaborators = (
                    Collaborator.objects.filter(
                        is_active=True,
                        function__in=(Collaborator.Function.OPERATIONAL, Collaborator.Function.ESTIMATOR),
                    )
                    .only('id', 'name')
                    .order_by('name')
                )
                return render(
                    request,
                    'core/timeclock_register.html',
                    {
                        'next_url': reverse('budgets:kanban_today'),
                        'token': (request.GET.get('token') or '').strip(),
                        'collaborator_name': '',
                        'interval_seconds': _timeclock_interval_seconds(),
                        'auto_redirect_seconds': 0,
                        'kiosk_mode': True,
                        'eligible_collaborators': list(collaborators),
                        'selected_collaborator_id': (request.GET.get('collaborator_id') or '').strip(),
                    },
                )
            messages.error(request, 'Seu usuário não está habilitado para registrar ponto.')
            return redirect('core:dashboard')
        return render(
            request,
            'core/timeclock_register.html',
            {
                'next_url': reverse('budgets:kanban_today'),
                'token': (request.GET.get('token') or '').strip(),
                'collaborator_name': collaborator.name,
                'interval_seconds': _timeclock_interval_seconds(),
                'auto_redirect_seconds': 30,
                'kiosk_mode': False,
            },
        )

    def post(self, request):
        user = getattr(request, 'user', None)
        if getattr(user, 'role', None) == CustomUser.Role.PONTO:
            collaborator = _get_eligible_collaborator_for_ponto_request(request)
            if collaborator is None:
                messages.error(request, 'Selecione o colaborador antes de confirmar.')
                return redirect('core:timeclock_ponto')

            token = (request.POST.get('token') or '').strip()
            code = (request.POST.get('code') or '').strip()
            next_url = (request.POST.get('next') or '').strip() or reverse('budgets:kanban_today')

            bucket = _timeclock_validate_token(token)
            if bucket is None:
                bucket = _timeclock_validate_code(code)
            if bucket is None:
                messages.error(request, 'QR Code/código inválido ou expirado.')
                return redirect('core:timeclock_ponto')

            today = timezone.localdate()
            now = timezone.now()
            now_local = timezone.localtime(now)
            earliest_dt = _timeclock_scheduled_dt(today, _timeclock_earliest_entry_time())
            start_dt = _timeclock_scheduled_dt(today, _timeclock_expected_start_time())
            end_dt = _timeclock_scheduled_dt(today, _timeclock_expected_end_time())
            grace_dt = _timeclock_scheduled_dt(today, _timeclock_extra_grace_time())
            with transaction.atomic():
                day, _ = TimeClockDay.objects.select_for_update().get_or_create(collaborator=collaborator, date=today)
                if day.entry_at is None:
                    if now_local < earliest_dt:
                        messages.error(request, 'Registro de entrada liberado a partir de 07:30.')
                        return redirect('core:timeclock_ponto')
                    day.entry_at = now
                    day.minutes_late = max(_timeclock_minutes_between(start_dt, now_local), 0)
                    day.minutes_extra = 0
                    day.minutes_missing = 0
                    day.minutes_worked = 0
                    day.save(update_fields=['entry_at', 'minutes_late', 'minutes_extra', 'minutes_missing', 'minutes_worked', 'updated_at'])
                    messages.success(request, f'Entrada registrada: {collaborator.name}.')
                elif day.exit_at is None:
                    day.exit_at = now
                    delta = 0
                    if day.entry_at:
                        delta = int((day.exit_at - day.entry_at).total_seconds() // 60)
                    day.minutes_worked = max(delta, 0)
                    if now_local > grace_dt:
                        day.minutes_extra = max(_timeclock_minutes_between(end_dt, now_local), 0)
                        day.minutes_missing = 0
                    elif now_local >= end_dt:
                        day.minutes_missing = 0
                        day.minutes_extra = 0
                    else:
                        day.minutes_missing = max(_timeclock_minutes_between(now_local, end_dt), 0)
                        day.minutes_extra = 0
                    day.save(update_fields=['exit_at', 'minutes_worked', 'minutes_extra', 'minutes_missing', 'updated_at'])
                    messages.success(request, f'Saída registrada: {collaborator.name}.')
                else:
                    messages.info(request, f'Jornada já concluída hoje: {collaborator.name}.')

            return redirect(next_url)

        return TimeClockRegisterView().post(request)


class TimeClockReportView(RoleRequiredMixin, View):
    allowed_roles = (CustomUser.Role.MANAGER, CustomUser.Role.FINANCE)

    def get(self, request):
        today = timezone.localdate()
        month_raw = (request.GET.get('month') or '').strip()
        selected_month = today.replace(day=1)
        if month_raw:
            try:
                year, month = [int(x) for x in month_raw.split('-', 1)]
                selected_month = selected_month.replace(year=year, month=month, day=1)
            except Exception:
                selected_month = today.replace(day=1)

        first_day = selected_month.replace(day=1)
        next_month = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1)
        last_day = next_month - timedelta(days=1)

        collaborator_id = (request.GET.get('collaborator_id') or '').strip()

        qs = (
            TimeClockDay.objects.select_related('collaborator')
            .filter(date__gte=first_day, date__lte=last_day)
            .order_by('date', 'collaborator__name', 'id')
        )
        if collaborator_id:
            qs = qs.filter(collaborator_id=collaborator_id)

        collaborators = (
            Collaborator.objects.filter(
                is_active=True,
                function__in=(Collaborator.Function.OPERATIONAL, Collaborator.Function.ESTIMATOR),
            )
            .only('id', 'name')
            .order_by('name')
        )

        total_minutes = 0
        total_minutes_late = 0
        total_minutes_extra = 0
        total_minutes_missing = 0
        for d in qs:
            total_minutes += int(d.minutes_worked or 0)
            total_minutes_late += int(d.minutes_late or 0)
            total_minutes_extra += int(d.minutes_extra or 0)
            total_minutes_missing += int(getattr(d, 'minutes_missing', 0) or 0)
        total_hours = round(total_minutes / 60, 2) if total_minutes else 0

        return render(
            request,
            'core/timeclock_report.html',
            {
                'days': list(qs),
                'collaborators': list(collaborators),
                'selected_month': first_day,
                'selected_collaborator_id': collaborator_id,
                'total_minutes_worked': total_minutes,
                'total_hours_worked': total_hours,
                'total_minutes_late': total_minutes_late,
                'total_minutes_extra': total_minutes_extra,
                'total_minutes_missing': total_minutes_missing,
            },
        )

from django.shortcuts import redirect
from django.urls import resolve, Resolver404

from users.views import DEFAULT_PASSWORD


class ForceDefaultPasswordChangeMiddleware:
    WHITELIST_VIEW_NAMES = {
        'users:password_change',
        'users:password_change_done',
        'users:login',
        'users:logout',
        'core:public_index',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def _is_whitelisted(self, request):
        view_name = getattr(getattr(request, 'resolver_match', None), 'view_name', None)
        if view_name is None:
            try:
                view_name = resolve(request.path_info).view_name
            except Resolver404:
                view_name = None
        if view_name and view_name in self.WHITELIST_VIEW_NAMES:
            return True
        path = getattr(request, 'path', '') or ''
        if path.startswith('/static/'):
            return True
        if path.startswith('/media/'):
            return True
        return False

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if (
            user
            and getattr(user, 'is_authenticated', False)
            and not self._is_whitelisted(request)
            and getattr(user, 'check_password', None)
            and user.check_password(DEFAULT_PASSWORD)
        ):
            from django.contrib import messages
            from django.contrib.messages import get_messages
            existing_tags = {str(m.level_tag) for m in get_messages(request)}
            if 'warning' not in existing_tags:
                messages.warning(
                    request,
                    'Sua senha está no padrão 123456. Troque a senha para continuar (mín. 8 caracteres e não pode ser só números).',
                )
            return redirect('users:password_change')
        return self.get_response(request)


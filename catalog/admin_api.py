"""Token-protected operator endpoints, so the site can be deployed, synced and
inspected remotely over HTTPS (the production box has no operator shell access).
Patterns copied from the battle-tested sysquant admin API:
  * deploy.sh must restart its own gunicorn unit as its LAST line, because a
    child launched from a worker stays in the unit's cgroup and dies with it;
  * git fetch + reset --hard, never bare pull, so a stale token or drifted
    tree fails loudly instead of no-opping;
  * API_BUILD is bumped each deploy so the operator can confirm new code is live.
"""
import functools
import json
import os
import subprocess
import sys

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

API_BUILD = '2026-10-16-smtp-test'


def _token_from(request):
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        return header[7:]
    return request.headers.get('X-Admin-Token', '')


def admin_token_required(view):
    @functools.wraps(view)
    @csrf_exempt
    def wrapped(request, *args, **kwargs):
        expected = settings.PQL_ADMIN_TOKEN
        if not expected:
            return JsonResponse({'error': 'PQL_ADMIN_TOKEN not configured'}, status=503)
        if _token_from(request) != expected:
            return JsonResponse({'error': 'invalid token'}, status=403)
        return view(request, *args, **kwargs)
    return wrapped


def _git_head():
    try:
        p = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                           cwd=settings.BASE_DIR, capture_output=True,
                           text=True, timeout=10)
        return p.stdout.strip() or p.stderr.strip()
    except OSError as exc:
        return f'unavailable: {exc}'


def _launch_detached(argv, log_name):
    """Start a job that must survive this worker. The log lands in PQL_LOG_DIR
    and is readable via the logs endpoint."""
    os.makedirs(settings.PQL_LOG_DIR, exist_ok=True)
    log_path = os.path.join(settings.PQL_LOG_DIR, log_name)
    log_file = open(log_path, 'ab')
    log_file.write(f"\n===== {' '.join(argv)} =====\n".encode())
    log_file.flush()
    subprocess.Popen(argv, cwd=settings.BASE_DIR, stdin=subprocess.DEVNULL,
                     stdout=log_file, stderr=log_file, start_new_session=True)
    return log_name


@admin_token_required
def health(request):
    from catalog.management.commands.catalog_stats import catalog_stats
    return JsonResponse({
        'api_build': API_BUILD,
        'git_head': _git_head(),
        'debug': settings.DEBUG,
        **catalog_stats(),
    })


@admin_token_required
def deploy(request):
    """POST: run deploy/deploy.sh detached. The script does everything before
    restarting the service, so the part of it the restart kills is only the
    final echo. Progress: GET logs/?file=deploy.log"""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    script = os.path.join(settings.BASE_DIR, 'deploy', 'deploy.sh')
    log_name = _launch_detached(['/bin/bash', script], 'deploy.log')
    return JsonResponse({'launched': True, 'git_head_before': _git_head(),
                         'log': f'api/admin/logs/?file={log_name}'})


@admin_token_required
def sync(request):
    """POST: refresh the catalog from the SysQuan database, detached."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    manage = os.path.join(settings.BASE_DIR, 'manage.py')
    log_name = _launch_detached(
        [sys.executable, '-u', manage, 'sync_catalog', '--from-db'], 'sync.log')
    return JsonResponse({'launched': True,
                         'log': f'api/admin/logs/?file={log_name}'})


@admin_token_required
def logs(request):
    name = os.path.basename(request.GET.get('file', 'deploy.log'))
    tail = min(int(request.GET.get('tail', 200)), 2000)
    path = os.path.join(settings.PQL_LOG_DIR, name)
    if not os.path.exists(path):
        return JsonResponse({'error': f'{name} not found'}, status=404)
    with open(path, 'rb') as f:
        lines = f.readlines()[-tail:]
    return HttpResponse(b''.join(lines), content_type='text/plain')


@admin_token_required
def test_email(request):
    """POST {"to": "addr"}: send one test mail so SMTP can be verified without
    emailing the real quote-notify recipients. Errors are returned, not
    swallowed, because this endpoint exists to diagnose the mail setup."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    from django.core.mail import send_mail
    to = (json.loads(request.body or b'{}').get('to') or '').strip()
    if not to:
        return JsonResponse({'error': '"to" address required'}, status=400)
    try:
        sent = send_mail('PeptiQuant Library SMTP test',
                         'Quote-request notification email is working.',
                         settings.DEFAULT_FROM_EMAIL, [to],
                         fail_silently=False)
    except Exception as exc:
        return JsonResponse({'sent': 0, 'error': f'{type(exc).__name__}: {exc}'},
                            status=502)
    return JsonResponse({'sent': sent, 'to': to,
                         'backend': settings.EMAIL_BACKEND,
                         'from': settings.DEFAULT_FROM_EMAIL})


@admin_token_required
def quote_requests(request):
    from catalog.models import QuoteRequest
    recent = []
    for quote in QuoteRequest.objects.prefetch_related('items')[:50]:
        recent.append({
            'id': quote.id,
            'created_at': quote.created_at.isoformat(),
            'name': quote.name, 'email': quote.email,
            'organization': quote.organization, 'status': quote.status,
            'message': quote.message,
            'items': [
                f'{i.peptide_sequence} '
                f'[{"+".join(f for f, on in [("SIS", i.want_sis), ("NAT", i.want_nat)] if on)}]'
                for i in quote.items.all()],
        })
    return JsonResponse({'quote_requests': recent})


@admin_token_required
def mark_quote_handled(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    from catalog.models import QuoteRequest
    body = json.loads(request.body or b'{}')
    updated = (QuoteRequest.objects.filter(id=body.get('id'))
               .update(status='handled'))
    return JsonResponse({'updated': updated})

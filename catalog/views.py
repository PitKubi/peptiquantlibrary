from django.conf import settings
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .models import CatalogPeptide, QuoteRequest, QuoteRequestItem

PAGE_SIZE = 50


def search(request):
    query = (request.GET.get('q') or '').strip()
    label_type = request.GET.get('label_type') or ''
    species = request.GET.get('species') or ''
    stock_only = request.GET.get('in_stock') == '1'

    peptides = CatalogPeptide.objects.all()
    if query:
        peptides = peptides.filter(
            Q(peptide_sequence__icontains=query)
            | Q(gene_name__icontains=query)
            | Q(uniprot_id__iexact=query)
            | Q(protein_name__icontains=query)
            | Q(mrmp_id__iexact=query))
    if label_type:
        peptides = peptides.filter(label_type=label_type)
    if species:
        peptides = peptides.filter(species__iexact=species)
    if stock_only:
        peptides = peptides.filter(in_stock=True)

    page = Paginator(peptides, PAGE_SIZE).get_page(request.GET.get('page'))
    species_options = (CatalogPeptide.objects.exclude(species='')
                       .values_list('species', flat=True).distinct().order_by('species'))
    return render(request, 'catalog/search.html', {
        'page': page,
        'query': query,
        'label_type': label_type,
        'species': species,
        'stock_only': stock_only,
        'species_options': species_options,
        'total': CatalogPeptide.objects.count(),
        'searched': bool(request.GET),
    })


def peptide_detail(request, peptide_id):
    peptide = get_object_or_404(CatalogPeptide, id=peptide_id)
    counterparts = (CatalogPeptide.objects
                    .filter(peptide_sequence=peptide.peptide_sequence)
                    .exclude(id=peptide.id))
    return render(request, 'catalog/detail.html',
                  {'peptide': peptide, 'counterparts': counterparts})


def quote_request(request):
    if request.method == 'POST' and request.POST.get('submit_request'):
        return _save_quote_request(request)
    peptide_ids = request.POST.getlist('peptide') or request.GET.getlist('peptide')
    peptides = CatalogPeptide.objects.filter(id__in=peptide_ids)
    return render(request, 'catalog/request.html', {'peptides': peptides})


def _save_quote_request(request):
    peptides = CatalogPeptide.objects.filter(
        id__in=request.POST.getlist('peptide'))
    name = (request.POST.get('name') or '').strip()
    email = (request.POST.get('email') or '').strip()
    if not name or not email or not peptides:
        return render(request, 'catalog/request.html', {
            'peptides': peptides,
            'error': 'Name, email and at least one peptide are required.',
            'name': name, 'email': email,
            'organization': request.POST.get('organization', ''),
            'message': request.POST.get('message', ''),
        })
    quote = QuoteRequest.objects.create(
        name=name, email=email,
        organization=(request.POST.get('organization') or '').strip(),
        message=(request.POST.get('message') or '').strip())
    for p in peptides:
        QuoteRequestItem.objects.create(
            request=quote, peptide=p,
            peptide_sequence=p.peptide_sequence, label_type=p.label_type,
            label_tag=p.label_tag, uniprot_id=p.uniprot_id, gene_name=p.gene_name)
    _notify_team(quote)
    return redirect('request_done')


def _notify_team(quote):
    if not settings.PQL_QUOTE_NOTIFY_EMAILS:
        return
    lines = [f'{i.peptide_sequence} [{i.label_type} {i.label_tag}] '
             f'{i.gene_name} {i.uniprot_id}' for i in quote.items.all()]
    body = (f'From: {quote.name} <{quote.email}>\n'
            f'Organization: {quote.organization}\n\n'
            f'Peptides:\n' + '\n'.join(lines) + f'\n\n{quote.message}')
    send_mail(f'PeptiQuant Library quote request #{quote.id}', body,
              settings.DEFAULT_FROM_EMAIL, settings.PQL_QUOTE_NOTIFY_EMAILS,
              fail_silently=True)


def request_done(request):
    return render(request, 'catalog/request_done.html')


def about(request):
    return render(request, 'catalog/about.html',
                  {'total': CatalogPeptide.objects.count()})

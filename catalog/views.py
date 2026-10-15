import re

from django.conf import settings
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from .models import (FastaAnnotation, PublicPeptide, QuoteRequest,
                     QuoteRequestItem)

PAGE_SIZE = 50
SELECTION_SESSION_KEY = 'selection'
BULK_TOKEN_LIMIT = 500


def _selection(request):
    return request.session.get(SELECTION_SESSION_KEY, [])


def _hero_stats():
    public = PublicPeptide.objects
    species_values = (public.exclude(species='')
                      .values_list('species', flat=True).distinct())
    # Count single species: a conserved 'Human + Mouse' row is not a species
    # of its own.
    species_count = len({part for value in species_values
                         for part in value.split(' + ')})
    return {
        'total': public.count(),
        'proteins': (public.exclude(uniprot_id='')
                     .values('uniprot_id').distinct().count()),
        'species_count': species_count,
    }


def search(request):
    query = (request.GET.get('q') or '').strip()
    availability = request.GET.get('availability') or ''
    species = request.GET.get('species') or ''
    bulk = (request.GET.get('bulk') or '').strip()

    peptides = PublicPeptide.objects.all()
    unmatched_tokens = []
    if bulk:
        peptides, unmatched_tokens = _bulk_filter(peptides, bulk)
    elif query:
        peptides = peptides.filter(
            Q(peptide_sequence__icontains=query)
            | Q(gene_name__icontains=query)
            | Q(uniprot_id__iexact=query)
            | Q(protein_name__icontains=query))
    if availability == 'sis':
        peptides = peptides.filter(sis_available=True)
    elif availability == 'nat':
        peptides = peptides.filter(nat_available=True)
    if species:
        # Component match, not equality: a conserved "Human + Mouse" peptide
        # belongs in both the Human and the Mouse filter.
        peptides = peptides.filter(species__icontains=species)

    page = Paginator(peptides, PAGE_SIZE).get_page(request.GET.get('page'))
    species_options = sorted({
        part for value in PublicPeptide.objects.exclude(species='')
        .values_list('species', flat=True).distinct()
        for part in value.split(' + ')
    })
    return render(request, 'catalog/search.html', {
        'page': page,
        'query': query,
        'bulk': bulk,
        'availability': availability,
        'species': species,
        'species_options': species_options,
        'unmatched_tokens': unmatched_tokens,
        'selection_count': len(_selection(request)),
        'filtered': bool(query or bulk or availability or species),
        **_hero_stats(),
    })


def _bulk_filter(peptides, bulk):
    """Match a pasted target list: each token against gene, UniProt accession
    or exact sequence."""
    tokens = [t for t in re.split(r'[\s,;]+', bulk) if t][:BULK_TOKEN_LIMIT]
    matched = Q()
    unmatched = []
    for token in tokens:
        token_q = (Q(gene_name__iexact=token) | Q(uniprot_id__iexact=token)
                   | Q(peptide_sequence__iexact=token))
        if PublicPeptide.objects.filter(token_q).exists():
            matched |= token_q
        else:
            unmatched.append(token)
    if not matched:
        return peptides.none(), unmatched
    return peptides.filter(matched), unmatched


def peptide_detail(request, sequence):
    peptide = get_object_or_404(PublicPeptide,
                                peptide_sequence=sequence.strip().upper())
    return render(request, 'catalog/detail.html', {
        'peptide': peptide,
        'protein_view': _protein_with_peptide_highlighted(peptide),
        'selection_count': len(_selection(request)),
    })


def _protein_with_peptide_highlighted(peptide):
    """Segments of the assigned protein's sequence with the peptide's
    occurrences marked, for the detail page."""
    annotation = FastaAnnotation.objects.filter(
        peptide_sequence=peptide.peptide_sequence).first()
    if annotation is None or not annotation.protein_sequence:
        return None
    protein = annotation.protein_sequence
    target = peptide.peptide_sequence
    segments = []
    positions = []
    cursor = 0
    while True:
        found = protein.find(target, cursor)
        if found < 0:
            break
        if found > cursor:
            segments.append({'text': protein[cursor:found], 'match': False})
        segments.append({'text': target, 'match': True})
        positions.append(found + 1)
        cursor = found + len(target)
    if cursor < len(protein):
        segments.append({'text': protein[cursor:], 'match': False})
    if not positions and annotation.match_start is not None:
        # Variant peptide: no exact occurrence; highlight the aligned region
        # of the parent protein instead.
        start = annotation.match_start
        segments = [{'text': protein[:start], 'match': False},
                    {'text': protein[start:start + len(target)], 'match': True},
                    {'text': protein[start + len(target):], 'match': False}]
        positions = [start + 1]
    return {
        'accession': annotation.uniprot_id,
        'length': len(protein),
        'positions': positions,
        'segments': segments,
        'variant_match': annotation.variant_match,
        'match_count': annotation.match_count,
    }


def selection(request):
    """Session-backed 'Your list'. action=add|remove|clear; add takes the
    peptide checkboxes of the submitting form, remove a single id."""
    if request.method != 'POST':
        return redirect('search')
    action = request.POST.get('action', 'add')
    current = _selection(request)
    if action == 'add':
        for peptide_id in request.POST.getlist('peptide'):
            if peptide_id.isdigit() and int(peptide_id) not in current:
                current.append(int(peptide_id))
    elif action == 'remove':
        peptide_id = request.POST.get('peptide', '')
        if peptide_id.isdigit() and int(peptide_id) in current:
            current.remove(int(peptide_id))
    elif action == 'clear':
        current = []
    request.session[SELECTION_SESSION_KEY] = current
    next_url = request.POST.get('next', '')
    if next_url.startswith('/'):
        return redirect(next_url)
    return redirect('quote_request' if action != 'add' else 'search')


def quote_request(request):
    if request.method == 'POST' and request.POST.get('submit_request'):
        return _save_quote_request(request)
    peptides = PublicPeptide.objects.filter(id__in=_selection(request))
    return render(request, 'catalog/request.html', {
        'peptides': peptides,
        'selection_count': len(_selection(request)),
    })


def _save_quote_request(request):
    peptides = list(PublicPeptide.objects.filter(id__in=_selection(request)))
    name = (request.POST.get('name') or '').strip()
    email = (request.POST.get('email') or '').strip()
    wanted = [
        (p, request.POST.get(f'sis_{p.id}') == 'on',
            request.POST.get(f'nat_{p.id}') == 'on')
        for p in peptides
    ]
    wanted = [(p, sis, nat) for p, sis, nat in wanted if sis or nat]
    if not name or not email or not wanted:
        return render(request, 'catalog/request.html', {
            'peptides': peptides,
            'error': 'Name, email and at least one selected form (SIS or NAT) are required.',
            'name': name, 'email': email,
            'organization': request.POST.get('organization', ''),
            'message': request.POST.get('message', ''),
            'selection_count': len(peptides),
        })
    quote = QuoteRequest.objects.create(
        name=name, email=email,
        organization=(request.POST.get('organization') or '').strip(),
        message=(request.POST.get('message') or '').strip())
    for peptide, want_sis, want_nat in wanted:
        QuoteRequestItem.objects.create(
            request=quote, peptide=peptide,
            peptide_sequence=peptide.peptide_sequence,
            want_sis=want_sis, want_nat=want_nat,
            uniprot_id=peptide.uniprot_id, gene_name=peptide.gene_name)
    _notify_team(quote)
    request.session[SELECTION_SESSION_KEY] = []
    return redirect('request_done')


def _notify_team(quote):
    if not settings.PQL_QUOTE_NOTIFY_EMAILS:
        return
    lines = []
    for item in quote.items.all():
        forms = '+'.join(f for f, on in [('SIS', item.want_sis),
                                         ('NAT', item.want_nat)] if on)
        lines.append(f'{item.peptide_sequence} [{forms}] '
                     f'{item.gene_name} {item.uniprot_id}'.strip())
    body = (f'From: {quote.name} <{quote.email}>\n'
            f'Organization: {quote.organization}\n\n'
            f'Peptides:\n' + '\n'.join(lines) + f'\n\n{quote.message}')
    send_mail(f'PeptiQuant Library quote request #{quote.id}', body,
              settings.DEFAULT_FROM_EMAIL, settings.PQL_QUOTE_NOTIFY_EMAILS,
              fail_silently=True)


def request_done(request):
    return render(request, 'catalog/request_done.html',
                  {'selection_count': 0})


def about(request):
    return render(request, 'catalog/about.html', {
        'selection_count': len(_selection(request)),
        **_hero_stats(),
    })

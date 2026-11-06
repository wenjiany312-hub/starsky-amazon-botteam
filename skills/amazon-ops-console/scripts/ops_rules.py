"""Only supplied category/product rules; deterministic matches, human review."""
import re
import unicodedata


def _normal(text):
    return unicodedata.normalize('NFKC',text).casefold()


def _matches(text,term):
    needle=_normal(term).strip()
    if not needle: return []
    # Chinese phrases have no whitespace word separators. Latin/digit terms do.
    def latin_word(char):
        return char=='_' or char.isdigit() or 'LATIN' in unicodedata.name(char,'')
    start=r'(?<!\w)' if latin_word(needle[0]) else ''
    end=r'(?!\w)' if latin_word(needle[-1]) else ''
    return list(re.finditer(start+re.escape(needle)+end,_normal(text)))


def check_listing(text_fields,rules):
    if not isinstance(text_fields,dict) or any(not isinstance(v,str) for v in text_fields.values()): raise ValueError('text_fields_invalid')
    if not isinstance(rules,dict): raise ValueError('rules_invalid')
    findings=[]
    for category in ('banned_terms','competitor_brands','unsupported_claims'):
        terms=rules.get(category) or []
        if not isinstance(terms,list): raise ValueError('rules_invalid')
        for entry in terms:
            entry={'term':entry} if isinstance(entry,str) else entry
            if not isinstance(entry,dict) or not isinstance(entry.get('term'),str) or not entry['term'].strip(): raise ValueError('rule_term_invalid')
            evidence=entry.get('evidence_hashes') or []
            supported=bool(evidence) and isinstance(evidence,list) and all(isinstance(h,str) and re.fullmatch('[0-9a-fA-F]{64}',h) for h in evidence)
            for field,text in text_fields.items():
                matches=_matches(text,entry['term'])
                if matches:
                    findings.append({'field':field,'term':entry['term'],'category':category,'matched_count':len(matches),
                                     'location_basis':'field_only_NFKC_casefold_no_original_offsets','rule_version':rules.get('version'),
                                     'status':'evidence_supplied_pending_review' if category=='unsupported_claims' and supported else 'pending_review',
                                     'blocked':category!='unsupported_claims' or not supported,
                                     'evidence_hashes':evidence if supported else [],'approval_required':True})
    return findings


def keyword_coverage(text_fields,keywords,rules=None):
    if not isinstance(keywords,list) or any(not isinstance(k,str) or not k.strip() for k in keywords): raise ValueError('keywords_invalid')
    check_listing(text_fields,rules or {})
    result={'covered':[],'missing':[],'blocked':[],'automatic_insertion':False}
    seen=set()
    for keyword in keywords:
        key=_normal(keyword).strip()
        if key in seen: continue
        seen.add(key)
        if any(f['blocked'] for f in check_listing({'keyword':keyword},rules or {})):
            result['blocked'].append(keyword);continue
        fields=[field for field,text in text_fields.items() if _matches(text,keyword)]
        if fields: result['covered'].append({'keyword':keyword,'fields':fields})
        else: result['missing'].append(keyword)
    return result

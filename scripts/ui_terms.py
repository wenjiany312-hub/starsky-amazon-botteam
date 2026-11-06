"""Translate internal prose only; preserve literal links, source evidence and S5 behavior."""
import json,re
from html.parser import HTMLParser
from pathlib import Path
RULES=json.loads((Path(__file__).resolve().parents[1]/'templates/user_visible_terms.json').read_text(encoding='utf-8-sig'))

def prose(text):
    for pattern,replacement in RULES.items():text=re.sub(r'(?<![\w-])(?:'+pattern+r')(?![\w-])',replacement,text,flags=re.I)
    return text

def rewrite(text):
    body,sep,evidence=str(text).partition('## 证据与出处')
    # Do not alter literal data or link targets while simplifying explanatory prose.
    parts=re.split(r'(`[^`\n]+`|\]\([^\n]+\))',body)
    return ''.join(x if i%2 else prose(x) for i,x in enumerate(parts))+sep+evidence

def scan(text):
    body=str(text).partition('## 证据与出处')[0];body=re.sub(r'`[^`\n]+`|\]\([^\n]+\)','',body)
    return [key for key in RULES if re.search(r'(?<![\w-])(?:'+key+r')(?![\w-])',body,re.I)]

class VisibleHTML(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=False);self.parts=[];self.stack=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs);skip=tag in {'script','style','code','pre'} or bool(re.search('evidence|technical-meta|source',a.get('id','')+' '+a.get('class',''),re.I))
        if tag not in {'meta','link','img','br','hr','input','wbr'}:self.stack.append((tag,skip))
        self.parts.append(self.get_starttag_text())
    def handle_startendtag(self,tag,attrs):self.parts.append(self.get_starttag_text())
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:self.stack=self.stack[:i];break
        self.parts.append('</'+tag+'>')
    def handle_data(self,text):self.parts.append(text if any(x[1] for x in self.stack) else prose(text))
    def handle_entityref(self,name):self.parts.append('&'+name+';')
    def handle_charref(self,name):self.parts.append('&#'+name+';')
    def handle_decl(self,decl):self.parts.append('<!'+decl+'>')
    def handle_comment(self,text):self.parts.append('<!--'+text+'-->')

def rewrite_html(text):
    parser=VisibleHTML();parser.feed(text);return ''.join(parser.parts)

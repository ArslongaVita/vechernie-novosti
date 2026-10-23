"""Collect a dated Russian-language issue; no paid API or credentials required."""
import concurrent.futures, datetime as dt, email.utils, html, json, re
import urllib.request, xml.etree.ElementTree as ET
from pathlib import Path
from difflib import SequenceMatcher

UTC=dt.timezone.utc
NOW=dt.datetime.now(UTC)
STOP=set('который которые после перед также этого сегодня сообщил сообщила сообщает заявил заявила россии россия российский российские года стало будет новости'.split())
def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'VecherNews/1.0 (public RSS reader)'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=25) as response:
        return response.read(3000000).decode('utf-8')
def words(text):
    return {w[:7] for w in re.findall(r'[а-яёa-z0-9]+',text.lower()) if len(w)>3 and w not in STOP}
def similar(a,b):
    wa,wb=words(a),words(b)
    return len(wa&wb)/max(1,min(len(wa),len(wb)))>=.65 or SequenceMatcher(None,a.lower(),b.lower()).ratio()>.72
def feed(section):
    topic='WORLD' if section=='world' else 'NATION'
    url=f'https://news.google.com/rss/headlines/section/topic/{topic}?hl=ru&gl=RU&ceid=RU:ru'
    root=ET.fromstring(fetch(url));result=[]
    for rank,item in enumerate(root.findall('./channel/item')):
        try:
            date=email.utils.parsedate_to_datetime(item.findtext('pubDate')).astimezone(UTC)
            if not NOW-dt.timedelta(hours=24)<=date<=NOW+dt.timedelta(minutes=10):continue
            publisher=item.findtext('source') or 'Источник'
            title=html.unescape(item.findtext('title') or '').removesuffix(' - '+publisher).strip()
            link=item.findtext('link') or ''
            if not title or not link.startswith('https://'):continue
            related=[]
            for href,label,source in re.findall(r'<li><a[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?<font[^>]*>(.*?)</font>',item.findtext('description') or '',re.S):
                name=html.unescape(re.sub('<[^>]*>','',source)).strip()
                if href.startswith('https://') and name:related.append({'name':name,'url':html.unescape(href)})
            result.append({'title':title,'date':date.isoformat(),'publisher':publisher,'url':link,'rank':rank,'related':related})
        except (TypeError,ValueError):continue
    return result
def telegram(channel):
    raw=fetch('https://t.me/s/'+channel);posts=[]
    for block in raw.split('class="tgme_widget_message_wrap')[1:]:
        body=re.search(r'class="tgme_widget_message_text[^>]*>(.*?)</div>',block,re.S)
        stamp=re.search(r'<time[^>]*datetime="([^"]+)"',block)
        link=re.search(r'data-post="([a-zA-Z0-9_]+/\d+)"',block)
        if not body or not stamp or not link:continue
        date=dt.datetime.fromisoformat(stamp[1]).astimezone(UTC)
        if not NOW-dt.timedelta(hours=24)<=date<=NOW:continue
        text=html.unescape(re.sub('<[^>]+>',' ',body[1]))
        posts.append({'text':text,'name':channel+' · Telegram','url':'https://t.me/'+link[1],'publishedAt':date.isoformat()})
    return posts
def select(items,posts,excluded):
    groups=[]
    for item in items:
        if any(similar(item['title'],title) for title in excluded):continue
        group=next((g for g in groups if similar(g[0]['title'],item['title'])),None)
        if group is None:groups.append([item])
        else:group.append(item)
    groups.sort(key=lambda g:(-int(any(telegram_match(i['title'],p['text']) for i in g for p in posts)),-len({s['name'] for i in g for s in i['related']}|{i['publisher'] for i in g}),min(i['rank'] for i in g)))
    picked=[];publisher_counts={}
    for group in groups:
        lead=group[0];publisher=lead['publisher']
        if publisher_counts.get(publisher,0)>=2:continue
        sources=[]
        for item in group:
            if item['publisher'] not in [s['name'] for s in sources]:
                sources.append({'name':item['publisher'],'url':item['url']})
            for related in item['related']:
                if related['name'] not in [s['name'] for s in sources]:sources.append(related)
        for post in posts:
            # Telegram only supplements an independently found RSS story.
            # Require a strong match in a short post; never promote a rumor by itself.
            first=post['text'][:350]
            if telegram_match(lead['title'], first):sources.insert(0,{'name':post['name'],'url':post['url']})
        names=', '.join(s['name'] for s in sources[:3])
        picked.append({'title':lead['title'],'summary':'В подборке публикаций: '+names+'. Подробности и разные версии событий — по ссылкам ниже.','category':'В фокусе','sources':sources[:4],'publishedAt':lead['date']})
        publisher_counts[publisher]=publisher_counts.get(publisher,0)+1
        if len(picked)==5:break
    if len(picked)!=5:raise RuntimeError('Not enough fresh, distinct stories from diverse publishers; old issue retained')
    return picked
def telegram_match(title, text):
    a,b=words(title),words(text)
    return len(a & b)>=3 and len(a & b)/max(1,len(a))>=.5

def valid(issue):
    return isinstance(issue,dict) and all(len(issue.get(k,[]))==5 for k in ('world','russia'))

def main():
    folder=Path(__file__).parent
    path=folder/'issue.json'
    bundle_path=folder/'editions.json'
    moscow=NOW.astimezone(dt.timezone(dt.timedelta(hours=3)))
    editions=[]
    if bundle_path.exists():
        editions=json.loads(bundle_path.read_text(encoding='utf-8-sig'))['editions']
    elif path.exists():
        old=json.loads(path.read_text(encoding='utf-8-sig'))
        if valid(old):
            old['releaseAt']=old['updatedAt']
            stamp=dt.datetime.fromisoformat(old['updatedAt']).astimezone(dt.timezone(dt.timedelta(hours=3))).astimezone(moscow.tzinfo)
            if stamp.hour<20:old['date']=(stamp.date()-dt.timedelta(days=1)).isoformat()
            stamp=dt.datetime.fromisoformat(old['updatedAt']).astimezone(moscow.tzinfo)
            if stamp.hour<20:old['date']=(stamp.date()-dt.timedelta(days=1)).isoformat()
            editions=[old]
    # Prepare during the day; the page releases the prepared edition at 20:00.
    # After release freeze that issue, while recovering a missing issue on late runs.
    day=moscow.date()
    today=next((x for x in editions if x['date']==day.isoformat() and x.get('prepared')),None)
    if moscow.hour>=20 and today:
        day+=dt.timedelta(days=1)
    release=dt.datetime.combine(day,dt.time(20),tzinfo=moscow.tzinfo)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs={key:pool.submit(feed,key) for key in ['world','russia']}
        telegram_jobs={name:pool.submit(telegram,name) for name in ['rbc_news','kommersant','tass_agency']}
        sections={key:job.result() for key,job in jobs.items()}
        posts=[];errors=[]
        for name,job in telegram_jobs.items():
            try:posts.extend(job.result())
            except Exception:errors.append(name)
    world=select(sections['world'],posts,[])
    russia=select(sections['russia'],posts,[n['title'] for n in world])
    posts.sort(key=lambda x:x['publishedAt'],reverse=True)
    telegram_items=[]
    for post in posts:
        if len(telegram_items)>=6:break
        if sum(x['name']==post['name'] for x in telegram_items)>=2:continue
        title=' '.join(post['text'].split()[:18]).strip()
        telegram_items.append({k:post[k] for k in ('name','url','publishedAt')}|{'title':title+'…'})
    note='Автоматическая подборка русскоязычных СМИ. Telegram отмечен прямыми ссылками; свежие посты каналов доступны ниже. Рейтинг отражает освещение темы, а не число репостов.'
    if errors:note+=' Не удалось проверить: '+', '.join(errors)+'.'
    issue={'date':day.isoformat(),'releaseAt':release.isoformat(),'updatedAt':NOW.isoformat(),'prepared':True,'note':note,'world':world,'russia':russia,'telegram':telegram_items,'telegramStatus':{'checked':len(posts),'unavailable':errors}}
    editions=[x for x in editions if x['date']!=issue['date']]+[issue]
    editions=sorted(editions,key=lambda x:x.get('releaseAt',x['updatedAt']))[-5:]
    bundle={'editions':editions}
    def atomic(file,data):
        tmp=file.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(file)
    atomic(bundle_path,bundle)
    released=[x for x in editions if dt.datetime.fromisoformat(x.get('releaseAt',x['updatedAt']))<=NOW]
    if released:atomic(path,released[-1])
    # Embedded last-good editions make the initial page independent of a JSON request.
    page=folder/'index.html'
    page_text=page.read_text(encoding='utf-8-sig')
    embedded=json.dumps(bundle,ensure_ascii=False).replace('<',r'<')
    page_text=re.sub(r'(<script id="embedded-editions" type="application/json">).*?(</script>)',lambda m:m[1]+embedded+m[2],page_text,flags=re.S)
    page.write_text(page_text,encoding='utf-8')
    print(json.dumps({'preparedFor':day.isoformat(),'releaseAt':release.isoformat(),'telegramPostsChecked':len(posts),'telegramVisible':len(telegram_items),'telegramUnavailable':errors},ensure_ascii=False))
if __name__=='__main__':main()

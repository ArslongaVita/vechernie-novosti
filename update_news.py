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
    with urllib.request.urlopen(req,timeout=25) as response:
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
        posts.append({'text':text,'name':channel+' · Telegram','url':'https://t.me/'+link[1]})
    return posts
def select(items,posts,excluded):
    groups=[]
    for item in items:
        if any(similar(item['title'],title) for title in excluded):continue
        group=next((g for g in groups if similar(g[0]['title'],item['title'])),None)
        if group is None:groups.append([item])
        else:group.append(item)
    groups.sort(key=lambda g:(-len({s['name'] for i in g for s in i['related']}|{i['publisher'] for i in g}),min(i['rank'] for i in g)))
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
            if similar(lead['title'],first):sources.insert(1,{'name':post['name'],'url':post['url']})
        names=', '.join(s['name'] for s in sources[:3])
        picked.append({'title':lead['title'],'summary':'В подборке публикаций: '+names+'. Подробности и разные версии событий — по ссылкам ниже.','category':'В фокусе','sources':sources[:4],'publishedAt':lead['date']})
        publisher_counts[publisher]=publisher_counts.get(publisher,0)+1
        if len(picked)==5:break
    if len(picked)!=5:raise RuntimeError('Not enough fresh, distinct stories from diverse publishers; old issue retained')
    return picked
def main():
    path=Path(__file__).with_name('issue.json')
    moscow=NOW.astimezone(dt.timezone(dt.timedelta(hours=3)))
    if path.exists():
        try:
            current=json.loads(path.read_text(encoding='utf-8-sig'))
            published=dt.datetime.fromisoformat(current['updatedAt']).astimezone(moscow.tzinfo)
            if current.get('date')==moscow.date().isoformat() and published.date()==moscow.date() and published.hour>=20:
                print('Today evening issue already published; keeping it unchanged')
                return
        except (ValueError,KeyError,TypeError):pass
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs={key:pool.submit(feed,key) for key in ['world','russia']}
        telegram_jobs={name:pool.submit(telegram,name) for name in ['rbc_news','kommersant']}
        sections={key:job.result() for key,job in jobs.items()}
        posts=[];errors=[]
        for name,job in telegram_jobs.items():
            try:posts.extend(job.result())
            except Exception:errors.append(name)
    world=select(sections['world'],posts,[])
    russia=select(sections['russia'],posts,[n['title'] for n in world])
    note='Автоматический выпуск по русскоязычным RSS-лентам Google Новостей: приоритет темам из нескольких изданий и позиции в ленте. Это не рейтинг репостов. Ссылки СМИ открываются через Google Новости.'
    if errors:note+=' Telegram: часть публичных каналов недоступна; используются доступные источники.'
    elif not posts:note+=' В доступных Telegram-каналах свежие посты не найдены.'
    issue={'date':NOW.astimezone(dt.timezone(dt.timedelta(hours=3))).date().isoformat(),'updatedAt':NOW.isoformat(),'note':note,'world':world,'russia':russia}
    # Write only after both categories pass validation.
    path=Path(__file__).with_name('issue.json');tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(issue,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');tmp.replace(path)
    print(json.dumps({'date':issue['date'],'world':5,'russia':5,'telegramPostsChecked':len(posts),'telegramUnavailable':errors},ensure_ascii=False))
if __name__=='__main__':main()

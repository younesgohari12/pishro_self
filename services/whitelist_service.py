"""Resolve user-selected destinations only among the account's existing dialogs."""
from __future__ import annotations
import asyncio
import re
import unicodedata
from urllib.parse import urlparse
from telethon.utils import get_peer_id
from services.identity_service import DIGITS
from services.sender import _dialog_category, _target_title


class WhitelistError(ValueError):
    pass


def _normal(value):
    return ' '.join(unicodedata.normalize('NFKC',str(value)).strip().casefold().split())


def parse_reference(value):
    value=str(value).strip().translate(DIGITS)
    if not value or len(value)>300:
        raise WhitelistError('مقصد معتبر بفرستید.')
    if re.fullmatch(r'-?[0-9]{1,19}',value):
        number=int(value)
        if not number or abs(number)>=(1<<63):raise WhitelistError('آیدی خارج از محدوده است.')
        return 'number',number
    if value.startswith(('https://','http://','t.me/','telegram.me/','www.t.me/')):
        url=urlparse(value if '://' in value else 'https://'+value)
        if url.hostname not in {'t.me','telegram.me','www.t.me'}:
            raise WhitelistError('فقط لینک تلگرام پذیرفته می‌شود.')
        path=url.path.strip('/').split('/')
        if path[0]=='c' and len(path)>=2 and re.fullmatch(r'[0-9]{1,16}',path[1]):
            return 'peer',-(1000000000000+int(path[1]))
        if path[0]=='s' and len(path)>1:path=path[1:]
        if path[0].startswith('+') or path[0]=='joinchat':
            raise WhitelistError('برای گروه خصوصی، آیدی گروه یا یک پیام قابل‌شناسایی از آن بفرستید؛ لینک دعوت کافی نیست.')
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',path[0]):
            return 'username',path[0].casefold()
        raise WhitelistError('لینک تلگرام معتبر نیست.')
    if value.startswith('@'):
        if not re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{0,31}',value):
            raise WhitelistError('یوزرنیم معتبر نیست.')
        return 'username',value[1:].casefold()
    return 'name',_normal(value)


async def resolve_entries(client, text='', *, message=None, contact=None):
    if client is None or not client.is_connected():
        raise WhitelistError('ابتدا سلف‌بات را متصل و روشن کنید.')
    references=[]
    forward=getattr(message,'fwd_from',None)
    origin=getattr(forward,'from_id',None)
    if origin is not None:
        try:references=[('peer',int(get_peer_id(origin)))]
        except Exception:raise WhitelistError('مبدأ پیام قابل شناسایی نیست.') from None
    elif forward is not None:
        raise WhitelistError('مبدأ این پیام پنهان است؛ آیدی یا یوزرنیم مقصد را بفرستید.')
    elif contact is not None:
        uid=getattr(contact,'user_id',None)
        if type(uid) is not int or uid<=0:raise WhitelistError('مخاطب آیدی تلگرام قابل‌شناسایی ندارد؛ یوزرنیم یا آیدی را بفرستید.')
        references=[('peer',uid)]
    else:
        tokens=[line.strip() for line in text.splitlines() if line.strip()]
        if not 1<=len(tokens)<=20:
            raise WhitelistError('یک تا ۲۰ مقصد بفرستید؛ هر مقصد در یک خط.')
        references=[parse_reference(token) for token in tokens]
    async def visible_dialogs():
        found={}
        async for dialog in client.iter_dialogs():
            entity=dialog.entity
            kind=_dialog_category(entity)
            if not kind or getattr(entity,'left',False) or getattr(entity,'deactivated',False):continue
            peer_id=int(get_peer_id(entity))
            aliases={str(getattr(entity,'username','') or '').casefold()}
            aliases.update(str(x.username).casefold() for x in (getattr(entity,'usernames',None) or []) if getattr(x,'active',False))
            found[peer_id]={'peer_id':peer_id,'raw_id':int(entity.id),'kind':kind,
                            'title':_target_title(entity),'aliases':aliases}
        return list(found.values())
    try:dialogs=await asyncio.wait_for(visible_dialogs(),timeout=30)
    except Exception:raise WhitelistError('دریافت گفتگوهای حساب ممکن نشد؛ کمی بعد دوباره تلاش کنید.') from None
    resolved={}
    for mode,value in references:
        matches=[]
        for row in dialogs:
            if mode=='peer':ok=row['peer_id']==value
            elif mode=='number':ok=row['peer_id']==value or (value>0 and row['raw_id']==value)
            elif mode=='username':ok=value in row['aliases']
            else:ok=value in row['aliases'] or value==_normal(row['title'])
            if ok:matches.append(row)
        if len(matches)>1:
            raise WhitelistError('چند مقصد با این نام یا شماره پیدا شد؛ آیدی کامل یا یوزرنیم دقیق را بفرستید.')
        if not matches:
            raise WhitelistError('یکی از مقصدها در گفتگوهای همین حساب پیدا نشد؛ ابتدا گروه یا پیوی را در تلگرام خود باز کنید و دوباره تلاش کنید.')
        row=matches[0]
        resolved[row['peer_id']]={k:row[k] for k in ('peer_id','kind','title')}
    return list(resolved.values())

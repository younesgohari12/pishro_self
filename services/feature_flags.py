"""Reversible feature switches shared by commands and both panels."""
import config

TRANSLATE_DISABLED = '⏸ قابلیت ترجمه فعلاً غیرفعال است.'
CRYPTO_DISABLED = '⏸ قابلیت قیمت و تبدیل ارز فعلاً غیرفعال است.'


def enabled(feature):
    return bool(getattr(config, feature.upper() + '_ENABLED', False))


def disabled_callback_message(data):
    if isinstance(data, bytes):
        data = data.decode('utf-8', errors='replace')
    data = str(data or '')
    if data.startswith('translate_'):
        return 'ترجمه به سلف منتقل شده است: .ترجمه فارسی'
    if data == 'feat_translate' and not enabled('self_translate'):
        return TRANSLATE_DISABLED
    if data.startswith('icrypto_') and not enabled('self_crypto'):
        return CRYPTO_DISABLED
    if data.startswith('crypto_') and not enabled('crypto'):
        return CRYPTO_DISABLED
    return None


def filter_buttons(rows):
    result = []
    for row in rows:
        visible = [button for button in row
                   if not disabled_callback_message(getattr(button, 'data', None))]
        if visible:
            result.append(visible)
    return result

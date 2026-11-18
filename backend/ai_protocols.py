"""Supported model API wire formats; no credentials or content are logged."""
import re
from urllib.parse import quote

SUPPORTED_PROTOCOLS = ('openai_chat_completions', 'openai_responses', 'gemini_native', 'anthropic_messages')

class ProtocolError(ValueError):
    pass

def image_source(image):
    if not image:
        return None
    if image.startswith('data:'):
        match = re.fullmatch(r'data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=]+)', image)
        if not match:
            raise ProtocolError('Invalid image data')
        return {'type': 'base64', 'media_type': match.group(1), 'data': match.group(2)}
    if not image.startswith('https://'):
        raise ProtocolError('Invalid image URL')
    return {'type': 'url', 'url': image}

def request_parts(protocol, base_url, model_id, key, prompt, image, max_tokens):
    source = image_source(image)
    if protocol == 'openai_chat_completions':
        content = [{'type':'text','text':prompt}]
        if image: content.append({'type':'image_url','image_url':{'url':image}})
        return base_url + '/chat/completions', {'Authorization':'Bearer '+key}, {'model':model_id,'messages':[{'role':'user','content':content}],'max_tokens':max_tokens}
    if protocol == 'openai_responses':
        content = [{'type':'input_text','text':prompt}]
        if image: content.append({'type':'input_image','image_url':image})
        return base_url + '/responses', {'Authorization':'Bearer '+key}, {'model':model_id,'input':[{'role':'user','content':content}],'max_output_tokens':max_tokens}
    if protocol == 'anthropic_messages':
        content = [{'type':'text','text':prompt}]
        if source: content.append({'type':'image','source':source})
        return base_url + '/messages', {'x-api-key':key,'anthropic-version':'2023-06-01'}, {'model':model_id,'max_tokens':max_tokens,'messages':[{'role':'user','content':content}]}
    if protocol == 'gemini_native':
        # generateContent uses inline_data; a web URL must be fetched only under
        # the user-selected data_url transport, never silently downloaded.
        if source and source['type'] != 'base64': raise ProtocolError('Gemini requires data_url image transport')
        name=model_id.removeprefix('models/')
        if not name or '/' in name: raise ProtocolError('Invalid Gemini model ID')
        parts=[{'text':prompt}]
        if source: parts.append({'inline_data':{'mime_type':source['media_type'],'data':source['data']}})
        return base_url + '/models/' + quote(name, safe='') + ':generateContent', {'x-goog-api-key':key}, {'contents':[{'role':'user','parts':parts}],'generationConfig':{'maxOutputTokens':max_tokens}}
    raise ProtocolError('Unsupported API protocol')

def response_text(protocol, body):
    try:
        if protocol == 'openai_chat_completions': value=body['choices'][0]['message']['content']
        elif protocol == 'openai_responses': value=''.join(part.get('text','') for item in body['output'] if item.get('type') == 'message' for part in item.get('content',[]) if part.get('type') == 'output_text')
        elif protocol == 'anthropic_messages': value=''.join(x.get('text','') for x in body['content'] if x.get('type') == 'text')
        elif protocol == 'gemini_native': value=''.join(x.get('text','') for x in body['candidates'][0]['content']['parts'] if isinstance(x,dict))
        else: raise ProtocolError('Unsupported API protocol')
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ProtocolError('Invalid response') from None
    if not isinstance(value,str) or not value.strip(): raise ProtocolError('Empty response')
    return value.strip()

def discovery_headers(protocol, key):
    if protocol in ('openai_chat_completions', 'openai_responses'): return {'Authorization':'Bearer '+key}
    if protocol == 'anthropic_messages': return {'x-api-key':key,'anthropic-version':'2023-06-01'}
    if protocol == 'gemini_native': return {'x-goog-api-key':key}
    raise ProtocolError('Unsupported API protocol')

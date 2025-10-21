import re
from telegram import MessageEntity, User

def build_text_with_entities(text_template: str) -> (str, list[MessageEntity]):
    segments = []
    last_index = 0

    # --- FIX: Added re.DOTALL flag ---
    # This allows the content of tags (like <pre>) to span multiple lines.
    tag_regex = re.compile(
        r'<a href="([^"]+)">(.*?)</a>|'
        r'<a user_id="(\d+)">(.*?)</a>|'
        r'<emoji id="(\d+)">(.*?)</emoji>|'
        r'<(b|i|u|s|spoiler|code|pre|q)>(.*?)</\7>',
        re.DOTALL
    )
    
    for match in tag_regex.finditer(text_template):
        plain_text = text_template[last_index:match.start()]
        if plain_text:
            segments.append({'type': 'plain', 'content': plain_text})

        if match.group(1) is not None:
            segments.append({'type': 'link', 'url': match.group(1), 'content': match.group(2)})
        elif match.group(3) is not None:
            segments.append({'type': 'mention', 'user_id': int(match.group(3)), 'content': match.group(4)})
        elif match.group(5) is not None:
            segments.append({'type': 'emoji', 'custom_emoji_id': match.group(5), 'content': match.group(6)})
        else:
            segments.append({'type': match.group(7), 'content': match.group(8)})
        
        last_index = match.end()

    trailing_text = text_template[last_index:]
    if trailing_text:
        segments.append({'type': 'plain', 'content': trailing_text})

    clean_text = "".join(s['content'] for s in segments)
    entities = []
    offset = 0
    
    tag_map = {
        'b': MessageEntity.BOLD, 'i': MessageEntity.ITALIC, 'u': MessageEntity.UNDERLINE,
        's': MessageEntity.STRIKETHROUGH, 'spoiler': MessageEntity.SPOILER,
        'code': MessageEntity.CODE, 'pre': MessageEntity.PRE, 'q': MessageEntity.BLOCKQUOTE,
        'link': MessageEntity.TEXT_LINK, 'mention': MessageEntity.TEXT_MENTION,
        'emoji': MessageEntity.CUSTOM_EMOJI
    }

    for segment in segments:
        content_len_utf16 = len(segment['content'].encode('utf-16-le')) // 2
        
        if segment['type'] != 'plain':
            entity_args = {
                'offset': offset,
                'length': content_len_utf16
            }
            if segment['type'] == 'link':
                entity_args['type'] = tag_map['link']
                entity_args['url'] = segment['url']
            elif segment['type'] == 'mention':
                entity_args['type'] = tag_map['mention']
                entity_args['user'] = User(id=segment['user_id'], first_name=segment['content'], is_bot=False)
            elif segment['type'] == 'emoji':
                entity_args['type'] = tag_map['emoji']
                entity_args['custom_emoji_id'] = segment['custom_emoji_id']
            else:
                 entity_args['type'] = tag_map[segment['type']]
            
            entities.append(MessageEntity(**entity_args))
        
        offset += content_len_utf16

    return clean_text, entities

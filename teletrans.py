# -*- coding: utf-8 -*-

import asyncio
import json
import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler

import aiohttp
import emoji
import google.generativeai as genai
from azure.ai.translation.text import TextTranslationClient, TranslatorCredential
from azure.ai.translation.text.models import InputTextItem
from azure.core.exceptions import HttpResponseError
from google.cloud import translate_v2 as translate
from google.oauth2 import service_account
from lingua import LanguageDetectorBuilder, Language
from telethon import events
from telethon.sync import TelegramClient
from telethon.tl.types import MessageEntityBlockquote

workspace = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

# 创建一个logger
logger = logging.getLogger('my_logger')
logger.setLevel(logging.INFO)

# 创建一个handler，用于写入日志文件
handler = RotatingFileHandler('%s/log.txt' % workspace, maxBytes=20000000, backupCount=5)

# 定义handler的输出格式
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

# 给logger添加handler
logger.addHandler(handler)

# 创建一个handler，用于输出到控制台
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

# 给logger添加handler
logger.addHandler(stream_handler)

detector = LanguageDetectorBuilder.from_all_languages().with_preloaded_language_models().build()
all_langs = Language.all()
all_langs = {lang.iso_code_639_1.name.lower(): lang.name for lang in all_langs}


def load_config():
    # load config from json file, check if the file exists first
    if not os.path.exists('%s/config.json' % workspace):
        logger.error('config.json not found, created an empty one')
        exit()

    with open('%s/config.json' % workspace, 'r') as f:
        config = json.load(f)

    return config


def save_config():
    cfg['target_config'] = target_config
    with open('%s/config.json' % workspace, 'w') as f:
        json.dump(cfg, f, indent=2)


## configuration
cfg = load_config()
## telegram config
api_id = cfg['api_id']
api_hash = cfg['api_hash']
## Block quote will be collapsed if the length of the text exceeds this value
collapsed_length = cfg['collapsed_length'] if 'collapsed_length' in cfg else 0
## translation service
translation_service = cfg['translation_service']
## google config
google_config = cfg['google'] if 'google' in cfg else {}
google_creds = google_config['creds'] if 'creds' in google_config else ''
## azure config
azure_config = cfg['azure'] if 'azure' in cfg else {}
azure_key = azure_config['key'] if 'key' in azure_config else ''
azure_endpoint = azure_config['endpoint'] if 'endpoint' in azure_config else ''
azure_region = azure_config['region'] if 'region' in azure_config else ''
## deeplx config
deeplx_config = cfg['deeplx'] if 'deeplx' in cfg else {}
deeplx_url = deeplx_config['url'] if 'url' in deeplx_config else 'https://api.deeplx.org/translate'
## openai config
openai_config = cfg['openai'] if 'openai' in cfg else {}
openai_api_key = openai_config['api_key'] if 'api_key' in openai_config else ''
openai_url = openai_config['url'] if 'url' in openai_config else 'https://api.openai.com/v1/chat/completions'
openai_model = openai_config['model'] if 'model' in openai_config else 'gpt-3.5-turbo'
openai_prompt = openai_config['prompt'] if 'prompt' in openai_config else ''
openai_temperature = openai_config['temperature'] if 'temperature' in openai_config else 0.5
## gemini config
gemini_config = cfg['gemini'] if 'gemini' in cfg else {}
gemini_api_key = gemini_config['api_key'] if 'api_key' in gemini_config else ''
gemini_model = gemini_config['model'] if 'model' in gemini_config else ''
gemini_prompt = gemini_config['prompt'] if 'prompt' in gemini_config else ''
gemini_temperature = gemini_config['temperature'] if 'temperature' in gemini_config else 0.5
## target config
target_config = cfg['target_config'] if 'target_config' in cfg else {}

# 初始化Telegram客户端。
client = TelegramClient('%s/client' % workspace, api_id, api_hash)

# Google Translation Service Initialization
if translation_service == 'google':
    if not google_creds:
        logger.error("Google translation service configuration is missing")
        exit()
    google_credentials = service_account.Credentials.from_service_account_info(google_creds)
    google_client = translate.Client(credentials=google_credentials)

# Azure Translation Service Initialization
if translation_service == 'azure':
    if not azure_key or not azure_endpoint or not azure_region:
        logger.error("Azure translation service configuration is missing")
        exit()
    text_translator = TextTranslationClient(endpoint=azure_endpoint,
                                            credential=TranslatorCredential(azure_key, azure_region))

if translation_service == 'gemini':
    if not gemini_config or not gemini_api_key:
        logger.error("Gemini translation service configuration is missing")
    genai.configure(api_key=gemini_api_key)


def remove_links(text):
    # regrex pattern for URL
    url_pattern = r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+'
    # use re.sub to remove the URL from the text
    return re.sub(url_pattern, '', text).strip()


async def translate_text(text, source_lang, target_langs) -> {}:
    result = {}
    if emoji.purely_emoji(text):
        return result
    detect_lang = detector.detect_language_of(text).iso_code_639_1.name.lower()
    if detect_lang in target_langs and detect_lang != source_lang:
        return result
    async with aiohttp.ClientSession() as session:
        tasks = []
        text_without_link = remove_links(text)
        for target_lang in target_langs:
            if source_lang == target_lang:
                result[target_lang] = text
                continue
            if target_lang in ('en', 'ja') and openai_enable:
                    tasks.append(translate_openai(text, source_lang, target_lang, session))
            else:
                raise Exception(
                    f"Unknown translation service: {translation_service}. Available services: openai, google, azure, deeplx")
        # 并发执行翻译任务。
        for lang, text in await asyncio.gather(*tasks):
            result[lang] = text
    return result


# 翻译google API函数
async def translate_google(text, source_lang, target_lang, session):
    if isinstance(text, bytes):
        text = text.decode("utf-8")

    result = google_client.translate(text, target_language=target_lang, format_='text')
    logger.info("Text: {}".format(result["input"]))
    logger.info("Translation: {}".format(result["translatedText"]))
    logger.info("Detected source language: {}".format(result["detectedSourceLanguage"]))

    return target_lang, result["translatedText"]


# 翻译deeplx API函数
async def translate_deeplx(text, source_lang, target_lang, session):
    url = "https://api.deeplx.org/MgYjqp0Y7JiclFY5nZ4dEnzMVAsXOuCmn_8iJVLIJBc/translate"
    payload = {
        "text": text,
        "source_lang": source_lang,
        "target_lang": target_lang
    }
    start_time = time.time()
    async with session.post(url, json=payload) as response:
        logger.info(f"DeepL 翻译从 {source_lang} 至 {target_lang} 耗时: {time.time() - start_time}")
        if response.status != 200:
            logger.error(f"翻译失败：{response.status}")
            raise Exception(f"翻译失败")

        result = await response.json()
        if result['code'] != 200:
            logger.error(f"DeepL翻译失败：{result}")
            raise Exception(f"DeepL翻译失败")

    return target_lang, result['data']


# 翻译Azure API函数
async def translate_azure(text, source_lang, target_lang, session):
    try:
        source_language = source_lang
        target_languages = [target_lang]
        input_text_elements = [InputTextItem(text=text)]

        response = text_translator.translate(content=input_text_elements, to=target_languages,
                                             from_parameter=source_language)
        translation = response[0] if response else None

        if translation:
            for translated_text in translation.translations:
                logger.info(
                    f"Text was translated to: '{translated_text.to}' and the result is: '{translated_text.text}'.")
                return translated_text.to, translated_text.text

    except HttpResponseError as exception:
        if exception.error is not None:
            logger.error(f"Error Code: {exception.error.code}")
            logger.error(f"Message: {exception.error.message}")
        raise


# 翻译openai API函数
async def translate_openai(text, source_lang, target_lang, session):
    url = openai_url
    headers = {
        "Authorization": "Bearer %s" % openai_api_key,
        "Content-Type": "application/json"
    }
    # 根据目标语言调整系统消息
    if target_lang == 'en':
        system_content = 'If my text cannot be translated or contains nonsencial content, just repeat my words precisely. As an American English expert, you\'ll help users express themselves clearly. You\'re not just translating, but rephrasing to maintain clarity. Use plain English and common idioms, and vary sentence lengths for natural flow. Avoid regional expressions. Respond with the translated sentence.'
    elif target_lang == 'ja':
        system_content = 'As a language expert, you are proficient in Chinese and Japanese. If my text cannot be translated or contains nonsencial content, just repeat my words precisely. You\'ll help users express themselves clearly. You\'re not just translating, but rephrasing to maintain clarity. Use plain Japanese and common idioms, and vary sentence lengths for natural flow. Avoid regional expressions. Respond with the translated sentence.'
    else:
        raise ValueError(f"Unsupported target language: {target_lang}")
    payload = {
        'messages': [
            {
            'role': 'system',
            'content': system_content
            },
            {
                'role': 'user',
                'content': text,
            }
        ],
        'stream': False,
        'model': openai_model,
        'temperature': openai_temperature,
        'presence_penalty': 0,
        'frequency_penalty': 0,
        'top_p': 1
    }

    start_time = time.time()
    async with session.post(url, headers=headers, data=json.dumps(payload)) as response:
        logger.info(f"OpenAI 翻译从 {source_lang} 至 {target_lang} 耗时: {time.time() - start_time}")
        response_text = await response.text()
        result = json.loads(response_text)
        try:
            return target_lang, result['choices'][0]['message']['content']
        except Exception as e:
            raise Exception(f"OpenAI 翻译失败：{response_text} {e}")


async def translate_gemini(text, source_lang, target_lang, session):
    prompt = gemini_prompt.replace('tgt_lang', all_langs.get(target_lang, target_lang))
    model = genai.GenerativeModel(gemini_model, system_instruction=prompt)
    response = model.generate_content(text, safety_settings="block_none",
                                      generation_config=genai.types.GenerationConfig(temperature=gemini_temperature))
    return target_lang, response.text.strip()


async def command_mode(event, target_key, text):
    if text.startswith('.tt-on-global') or text == '.tt-off-global':
        target_key = '0.%d' % event.sender_id
        text = text.replace('-global', '')

    if text == '.tt-off':
        await event.delete()
        if target_key in target_config:
            del target_config[target_key]
            save_config()
            logger.info("已禁用: %s" % target_key)
        return

    if text.startswith('.tt-on,'):
        _, source_lang, target_langs = text.split(',')
        if not source_lang or not target_langs:
            await event.message.edit("错误命令，正确格式: .tt-on,source_lang,target_lang1|target_lang2")
        else:
            target_config[target_key] = {
                'source_lang': source_lang,
                'target_langs': target_langs.split('|')
            }
            save_config()
            logger.info(f"设置成功: {target_config[target_key]}")
            await event.message.edit("设置成功: %s" % target_config[target_key])
        await asyncio.sleep(3)
        await event.message.delete()
        return

    if text.startswith('.tt-skip'):
        await event.message.edit(text[8:].strip())
        logger.info("跳过翻译")
        return

    if text.startswith('.tt-once,'):
        command, raw_text = text.split(' ', 1)
        _, source_lang, target_langs = command.split(',')
        logger.info(f"翻译消息: {raw_text}")
        await translate_and_edit(event.message, raw_text, source_lang, target_langs.split('|'))
        return

    await event.message.edit("未知命令")
    await asyncio.sleep(3)
    await event.message.delete()


# 同时监听新消息事件和编辑消息事件，进行消息处理。
@client.on(events.NewMessage(outgoing=True))
@client.on(events.MessageEdited(outgoing=True))
async def handle_message(event):
    target_key = '%d.%d' % (event.chat_id, event.sender_id)
    try:
        message = event.message
        # 忽略空消息。
        if not message.text:
            return
        message_content = message.text.strip()
        if not message_content:
            return

        # skip PagerMaid commands
        if message_content.startswith(','):
            return

        # skip bot commands
        if message_content.startswith('/'):
            return

        # command mode
        if message_content.startswith('.tt-'):
            await command_mode(event, target_key, message_content)
            return

        # handle reply message
        if message.reply_to_msg_id and message_content.startswith('.tt,'):
            _, source_lang, target_langs = message_content.split(',')
            logger.info(f"Reply message: {message.reply_to_msg_id}")
            reply_message = await client.get_messages(event.chat_id, ids=message.reply_to_msg_id)
            if not reply_message.text:
                return
            message_content = reply_message.text.strip()
            if source_lang and target_langs:
                logger.info(f"翻译消息: {message.text}")
                await translate_and_edit(message, message_content, source_lang, target_langs.split('|'))
            return

        # handle edited message
        if isinstance(event, events.MessageEdited.Event):
            if message_content.startswith('.tt'):
                message_content = message_content[3:].strip()
            else:
                return

        # chat config
        config = {}
        if target_key in target_config:
            config = target_config[target_key]
        else:
            # global config
            target_key = '0.%d' % event.sender_id
            if target_key not in target_config:
                return
            config = target_config[target_key]

        logger.info(f"翻译消息: {message.text}")
        source_lang = config['source_lang']
        target_langs = config['target_langs']
        await translate_and_edit(message, message_content, source_lang, target_langs)

    except Exception as e:
        # 记录处理消息时发生的异常。
        logger.error(f"Error handling message: {e}")


async def translate_and_edit(message, message_content, source_lang, target_langs):
    start_time = time.time()  # 记录开始时间
    translated_texts = await translate_text(message_content, source_lang, target_langs)
    logger.info(f"翻译耗时: {time.time() - start_time}")

    if not translated_texts:
        return

    modified_message = translated_texts[target_langs[0]]

    if len(target_langs) > 1:
        secondary_messages = []
        for lang in target_langs[1:]:
            secondary_messages.append(translated_texts[lang])

        modified_message += '\n%s' % '\n'.join(secondary_messages)

    # Handle special characters such as emojis and other unicode characters
    pattern = u'[\U00010000-\U0010ffff]'
    matches = len(re.findall(pattern, message_content))

    # Extract repeated computations
    translated_text = translated_texts[target_langs[0]]
    pattern_matches_translated = len(re.findall(pattern, translated_text))
    pattern_matches_modified = len(re.findall(pattern, modified_message))

    # Calculate offsets and lengths
    offset = len(translated_text) + pattern_matches_translated + 1
    length = len(modified_message) - len(translated_text) + pattern_matches_modified - pattern_matches_translated - 1

    if collapsed_length > 0 and len(modified_message) - offset > collapsed_length:
        # Create MessageEntityBlockquote with calculated values
        formatting_entities = [MessageEntityBlockquote(offset=offset, length=length, collapsed=True)]
    else:
        formatting_entities = [MessageEntityBlockquote(offset=offset, length=length)]

    # Edit the message
    await client.edit_message(message, modified_message, formatting_entities=formatting_entities)


# 启动客户端并保持运行。
try:
    client.start()
    client.run_until_disconnected()
finally:
    # 断开客户端连接。
    client.disconnect()

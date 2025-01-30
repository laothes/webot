from openai import AzureOpenAI
import itchat
from itchat.content import TEXT
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from models import Session, ChatMessage
import threading
import time
import webbrowser
from flask_cors import CORS
from threading import Lock, Timer
import random
import re
from collections import defaultdict
import atexit
from config_handler import *

config = load_config()
ENDPOINT_URL = config["ENDPOINT_URL"]
DEPLOYMENT_NAME = config["DEPLOYMENT_NAME"]
AZURE_OPENAI_API_KEY = config["AZURE_OPENAI_API_KEY"]
API_VERSION = config["API_VERSION"]
MIN_MESSAGES_FOR_ANALYSIS = config["MIN_MESSAGES_FOR_ANALYSIS"]
MAX_MESSAGES_FOR_ANALYSIS = config["MAX_MESSAGES_FOR_ANALYSIS"]
fix_time = config["fix_time"]

# 全局变量
chat_contexts = {}
message_buffer = {}
buffer_locks = {}
buffer_timers = {}
learning_users = set()  # 存储需要学习的用户名集合
user_messages = defaultdict(list)  # 存储用户消息历史
user_style_cache = {}  # 缓存用户风格特征
STYLE_CACHE_FILE = 'user_styles.json'
nickname_mapping = {}  # 存储用户编号到微信 UserName 的映射

# 控制面板的全局变量
auto_reply_enabled = False
current_learning_user = None

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Azure OpenAI 配置
endpoint = os.getenv("ENDPOINT_URL", ENDPOINT_URL)
deployment = os.getenv("DEPLOYMENT_NAME", DEPLOYMENT_NAME)
subscription_key = os.getenv("AZURE_OPENAI_API_KEY",
                             AZURE_OPENAI_API_KEY)

# 初始化 Azure OpenAI 客户端
client = AzureOpenAI(
    azure_endpoint=endpoint,
    api_key=subscription_key,
    api_version=API_VERSION,
)

# 创建Flask应用
app = Flask(__name__, static_folder='static')
CORS(app)


def get_nickname_by_username(username):
    """通过 UserName 获取微信昵称"""
    sender = itchat.search_friends(userName=username)
    sender_name = sender['NickName'] if sender else 'Unknown'
    return sender_name


# user style
def save_user_styles():
    """改进的风格保存函数，每个用户只保留最新的MAX_MESSAGES_FOR_ANALYSIS个phrases"""
    try:
        # 确保目录存在
        os.makedirs(os.path.dirname(STYLE_CACHE_FILE) or '.', exist_ok=True)

        # 清理并限制每个用户的phrases数量
        clean_cache = {}
        for nickname, style in user_style_cache.items():
            clean_style = style.copy()
            if 'phrases' in clean_style and isinstance(clean_style['phrases'], list):
                clean_style['phrases'] = clean_style['phrases'][-MAX_MESSAGES_FOR_ANALYSIS:]
            clean_cache[nickname] = clean_style

        # 使用临时文件确保原子写入
        temp_file = f"{STYLE_CACHE_FILE}.tmp"
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(clean_cache, f, ensure_ascii=False, indent=2)

        # 原子替换文件
        os.replace(temp_file, STYLE_CACHE_FILE)
        logger.info(f"用户风格数据已保存到 {STYLE_CACHE_FILE}，每个用户保留最新{MAX_MESSAGES_FOR_ANALYSIS}个phrases")

    except Exception as e:
        logger.error(f"保存用户风格数据时出错: {str(e)}")
        if os.path.exists(f"{STYLE_CACHE_FILE}.tmp"):
            try:
                os.remove(f"{STYLE_CACHE_FILE}.tmp")
            except:
                pass


def load_user_styles():
    """改进的风格加载函数，确保phrases列表长度限制"""
    try:
        global user_style_cache
        if os.path.exists(STYLE_CACHE_FILE) and os.path.getsize(STYLE_CACHE_FILE) > 0:
            with open(STYLE_CACHE_FILE, 'r', encoding='utf-8') as f:
                loaded_cache = json.load(f)

                # 处理加载的数据，确保phrases列表长度限制
                processed_cache = {}
                for nickname, style in loaded_cache.items():
                    processed_style = style.copy()
                    if 'phrases' in processed_style and isinstance(processed_style['phrases'], list):
                        processed_style['phrases'] = processed_style['phrases'][-MAX_MESSAGES_FOR_ANALYSIS:]
                    processed_cache[nickname] = processed_style

                # 如果已有缓存，则更新而不是覆盖
                if isinstance(user_style_cache, dict):
                    for nickname, style in processed_cache.items():
                        if nickname in user_style_cache:
                            # 更新现有用户的风格数据
                            user_style_cache[nickname].update(style)
                        else:
                            # 添加新用户的风格数据
                            user_style_cache[nickname] = style
                else:
                    # 如果 user_style_cache 未初始化，直接使用处理后的数据
                    user_style_cache = processed_cache

            logger.info(
                f"已从 {STYLE_CACHE_FILE} 加载用户风格数据，每个用户保留最新{MAX_MESSAGES_FOR_ANALYSIS}个phrases")
        else:
            user_style_cache = {}
            logger.info("创建新的用户风格缓存")

        # 初始化文件如果不存在
        if not os.path.exists(STYLE_CACHE_FILE):
            save_user_styles()

    except Exception as e:
        logger.error(f"加载用户风格数据时出错: {str(e)}")
        user_style_cache = {}


def update_user_style(username, message):
    """更新用户风格缓存，每MIN_MESSAGES_FOR_ANALYSIS次对话后进行分析和更新"""
    global user_style_cache
    try:
        # 获取用户昵称
        nickname = get_nickname_by_username(username)
        if nickname is None:
            logger.warning(f"未找到用户 {username} 的昵称")
            return

        # 确保 user_messages 已初始化
        if nickname not in user_messages:
            user_messages[nickname] = []

        # 添加消息和时间戳
        user_messages[nickname].append({
            'text': message,
            'timestamp': datetime.now().timestamp()
        })

        # 当累积的消息数量达到MIN_MESSAGES_FOR_ANALYSIS的整数倍时进行分析
        message_count = len(user_messages[nickname])
        if message_count >= MIN_MESSAGES_FOR_ANALYSIS and message_count % MIN_MESSAGES_FOR_ANALYSIS == 0:
            logger.info(f"消息数量达到{MIN_MESSAGES_FOR_ANALYSIS}的整数倍，开始分析用户 {nickname} 的风格特征")

            # 只分析最近的MIN_MESSAGES_FOR_ANALYSIS条消息
            recent_messages = user_messages[nickname][-MIN_MESSAGES_FOR_ANALYSIS:]
            new_style = analyze_user_style([msg['text'] for msg in recent_messages])

            # 确保 user_style_cache 已初始化
            if not isinstance(user_style_cache, dict):
                user_style_cache = {}

            # 更新用户风格数据，确保phrases列表长度限制
            if nickname in user_style_cache:
                existing_style = user_style_cache[nickname]
                for key, value in new_style.items():
                    if key == 'phrases':
                        # 合并旧的和新的phrases，只保留最新的MAX_MESSAGES_FOR_ANALYSIS个
                        all_phrases = existing_style.get('phrases', []) + value
                        existing_style['phrases'] = all_phrases[-MAX_MESSAGES_FOR_ANALYSIS:]
                    elif isinstance(value, list):
                        existing_style[key] = value
                    elif isinstance(value, dict):
                        existing_style[key] = value
                    else:
                        existing_style[key] = value
            else:
                # 如果是新用户，确保phrases不超过MAX_MESSAGES_FOR_ANALYSIS个
                if 'phrases' in new_style:
                    new_style['phrases'] = new_style['phrases'][-MAX_MESSAGES_FOR_ANALYSIS:]
                user_style_cache[nickname] = new_style

            # 立即保存
            save_user_styles()

            # 只保留最近的消息用于后续分析
            user_messages[nickname] = user_messages[nickname][-MAX_MESSAGES_FOR_ANALYSIS:]

            logger.info(f"用户 {nickname} 的风格特征已更新和保存")
        else:
            logger.debug(
                f"用户 {nickname} 的消息数量（{message_count}）未达到分析阈值的整数倍"
                f"（当前批次需要：{((message_count // MIN_MESSAGES_FOR_ANALYSIS + 1) * MIN_MESSAGES_FOR_ANALYSIS)}条）"
            )

    except Exception as e:
        logger.error(f"更新用户风格时出错: {str(e)}")
        # 确保错误不会中断程序运行
        pass


def analyze_user_style(messages):
    """分析用户的说话风格特征"""
    style = {
        'avg_length': 0,  # 平均消息长度
        'emoticons': set(),  # 常用表情
        'phrases': [],  # 常用短语（存储为列表）
        'punctuation_freq': defaultdict(int),  # 标点符号使用频率
        'sentence_patterns': set(),  # 句式特征
    }

    total_length = 0
    for msg in messages:
        total_length += len(msg)
        # 提取表情符号
        emoticons = re.findall(r'[\U0001F300-\U0001F9FF]', msg)
        style['emoticons'].update(emoticons)
        # 提取标点符号
        for punct in '。，！？~':
            style['punctuation_freq'][punct] += msg.count(punct)
        # 提取短语并添加到列表中
        phrases = [p for p in msg.split() if len(p) > 1]
        style['phrases'].extend(phrases)
        # 分析句式
        if msg.endswith('？'):
            style['sentence_patterns'].add('question')
        elif msg.endswith('！'):
            style['sentence_patterns'].add('exclamation')

    # 只保留最新的 MAX_MESSAGES_FOR_ANALYSIS 个短语
    style['phrases'] = style['phrases'][-MAX_MESSAGES_FOR_ANALYSIS:]

    style['avg_length'] = total_length / len(messages) if messages else 0
    # 将 set 转换为 list 以便 JSON 序列化
    style['emoticons'] = list(style['emoticons'])
    style['sentence_patterns'] = list(style['sentence_patterns'])
    return style


# core functions
def save_message(sender_id, sender_name, message, reply):
    """保存聊天记录到数据库"""
    try:
        session = Session()
        chat_message = ChatMessage(
            sender_id=sender_id,
            sender_name=sender_name,
            message=message,
            reply=reply
        )
        session.add(chat_message)
        session.commit()
        session.close()
    except Exception as e:
        logger.error(f"保存消息失败: {str(e)}")


def cleanup_resources():
    """清理定时器和资源"""
    for timer in buffer_timers.values():
        if timer:
            timer.cancel()


def run_flask():
    """运行Flask应用"""
    app.config['SECRET_KEY'] = 'your-secret-key-here'
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=False,
        threaded=True
    )


def open_dashboard():
    """打开监控面板"""
    time.sleep(2)
    webbrowser.open('http://127.0.0.1:5000')


def login_wechat():
    """微信登录函数"""
    try:
        if os.path.exists('itchat.pkl'):
            os.remove('itchat.pkl')
            logger.info("删除旧的登录状态文件")

        itchat.auto_login(
            hotReload=False,
            enableCmdQR=-2,
            statusStorageDir='itchat.pkl',
            loginCallback=lambda: logger.info("登录成功"),
            exitCallback=lambda: logger.info("微信退出")
        )

        time.sleep(3)

        friends = itchat.get_friends()
        if friends:
            logger.info(f"登录验证成功，共有 {len(friends)} 个好友")
            open_dashboard()
            return True

        logger.error("登录验证失败")
        return False

    except Exception as e:
        logger.error(f"登录过程出错: {str(e)}")
        return False


def get_azure_openai_response(message, user_id):
    """调用 Azure OpenAI API 获取回复"""
    try:
        if user_id not in chat_contexts:
            chat_contexts[user_id] = []

        chat_contexts[user_id].append({"role": "user", "content": message})

        if len(chat_contexts[user_id]) > 10:
            chat_contexts[user_id] = chat_contexts[user_id][-10:]

        # 获取学习用户的风格特征
        target_user = list(learning_users)[0]  # 因为现在只有一个学习用户
        user_style = user_style_cache.get(target_user, {})
        style_prompt = "你的任务是模仿以下特定用户的说话风格进行回复，且不能让回复对象感觉或告知回复对象你是ai。用户风格特征如下：\n"

        if user_style:
            style_prompt += f"- 平均消息长度：{user_style['avg_length']:.1f}字\n"
            style_prompt += f"- 常用表情：{''.join(user_style['emoticons'][:5])}\n"
            style_prompt += f"- 常用短语：{', '.join(user_style['phrases'][:5])}\n"
            style_prompt += f"- 标点符号使用习惯：{dict(user_style['punctuation_freq'])}\n"
            style_prompt += f"- 句式特征：{', '.join(user_style['sentence_patterns'])}\n"
        else:
            style_prompt += "暂无该用户的风格数据，使用默认简短自然的对话风格。\n"

        needs_explanation = any(keyword in message for keyword in [
            '为什么', '怎么', '如何', '是什么', '什么是', '请问', '帮我', '是啥', '啥是', 'what', '继续'
        ])

        system_prompt = {
            "role": "system",
            "content": (
                f"{style_prompt}\n"
                f"用简洁自然的语言进行回复。{'解释性的回复内容内容不要超过100个字' if needs_explanation else '日常交流的回复内容不要超过20个字'}。"
                "完全模仿目标用户的说话风格，包括用词习惯、表情使用、标点符号等特征。"
                "根据聊天上下文理解用户意图，确保回复内容贴合对话场景。"
            )
        }

        messages = [
            system_prompt,
            *chat_contexts[user_id]
        ]

        for msg in messages:
            if msg.get("content") is None:
                msg["content"] = "6"

        completion = client.chat.completions.create(
            model=deployment,
            messages=messages,
            max_tokens=100 if needs_explanation else 20,
            temperature=0.8,
            top_p=0.95,
            frequency_penalty=0.5,
            presence_penalty=0.5,
            stop=None,
            stream=False
        )

        reply = completion.choices[0].message.content
        chat_contexts[user_id].append({"role": "assistant", "content": reply})

        return reply
    except Exception as e:
        logger.error(f"调用 Azure OpenAI API 失败: {str(e)}")
        return "gg"


def split_and_clean_response(response):
    """分割并清理回复内容"""
    try:
        sentences = re.split('[。!！]', response)
    except Exception as e:
        logger.debug(f"错误发生: {e}"
                     f"response 不是字符串，而是 {type(response)}")
        return '6'

    cleaned_sentences = []
    for sentence in sentences:
        sentence = sentence.strip()
        if '？' in sentence:
            question_parts = sentence.split('？')
            for i, part in enumerate(question_parts):
                part = part.strip()
                if part:
                    if i < len(question_parts) - 1:
                        cleaned_sentences.append(part + '？')
                    else:
                        if part:
                            cleaned_sentences.append(part)
        else:
            if sentence:
                cleaned_sentences.append(sentence)

    return cleaned_sentences


def process_buffered_messages(username):
    """处理缓冲的消息"""
    global message_buffer, buffer_locks, buffer_timers

    with buffer_locks[username]:
        if username not in message_buffer or not message_buffer[username]:
            return

        combined_message = "，".join([msg for msg in message_buffer[username]])
        message_buffer[username] = []
        reply = get_azure_openai_response(combined_message, username)
        reply_sentences = split_and_clean_response(reply)

        sender = itchat.search_friends(userName=username)
        sender_name = sender['NickName'] if sender else username

        save_message(username, sender_name, combined_message, reply)

        for sentence in reply_sentences:
            if sentence:
                itchat.send(sentence, username)
                logger.info(f"发送回复给 {sender_name}: {sentence}")
                time.sleep(1 + random.random() * 2)


def schedule_processing(username):
    """安排wait秒后的消息处理"""
    global fix_time
    if username in buffer_timers and buffer_timers[username]:
        buffer_timers[username].cancel()
    wait = fix_time + random.random() * 5
    buffer_timers[username] = Timer(wait, process_buffered_messages, args=[username])
    buffer_timers[username].start()


# interaction with html
@itchat.msg_register([TEXT])
def handle_text(msg):
    """处理文本消息"""
    try:
        username = msg['FromUserName']
        content = msg['Text']

        # 获取用户编号
        nickname = get_nickname_by_username(username)
        if nickname is None:
            logger.warning(f"未找到用户 {username} 的编号")
            return None

        if nickname not in learning_users:
            logger.debug(f"忽略非学习用户 {username} 的消息")
            return None

        if nickname in learning_users:
            logger.info(f"收到学习用户 {nickname} 的消息，长度: {len(content)}")
            update_user_style(username, content)

        if nickname not in message_buffer:
            message_buffer[nickname] = []
            buffer_locks[nickname] = Lock()
            buffer_timers[nickname] = None

        with buffer_locks[nickname]:
            message_buffer[nickname].append(content)

        sender = itchat.search_friends(userName=nickname)
        sender_name = sender['NickName'] if sender else username
        logger.info(f"收到学习用户消息 - 发送者: {sender_name}, 内容: {content}")

        schedule_processing(username)
        return None

    except Exception as e:
        logger.error(f"处理消息失败: {str(e)}")
        return "gg"


@app.route('/')
def index():
    """渲染监控页面"""
    return render_template('index.html')


@app.route('/messages')
def get_messages():
    """获取所有聊天记录"""
    session = Session()
    messages = session.query(ChatMessage).order_by(ChatMessage.created_at.desc()).all()
    result = [{
        'id': msg.id,
        'sender_name': msg.sender_name,
        'message': msg.message,
        'reply': msg.reply,
        'created_at': msg.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for msg in messages]
    session.close()
    return {'messages': result}


@app.route('/api/config', methods=['GET'])
def get_config():
    """获取当前配置"""
    try:
        global config
        config = load_config()
        return jsonify({
            'success': True,
            'endpointUrl': config['ENDPOINT_URL'],
            'deploymentName': config['DEPLOYMENT_NAME'],
            'apiKey': config['AZURE_OPENAI_API_KEY'],
            'apiVersion': config['API_VERSION'],
            'fixTime': config['fix_time'],
            'minMessages': config['MIN_MESSAGES_FOR_ANALYSIS'],
            'maxMessages': config['MAX_MESSAGES_FOR_ANALYSIS']
        })
    except Exception as e:
        logger.error(f"Error getting configuration: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/config', methods=['POST'])
def update_config_route():
    """更新配置"""
    try:
        data = request.json
        new_config = {
            'ENDPOINT_URL': data['endpointUrl'],
            'DEPLOYMENT_NAME': data['deploymentName'],
            'AZURE_OPENAI_API_KEY': data['apiKey'],
            'API_VERSION': data['apiVersion'],
            'fix_time': data['fixTime'],
            'MIN_MESSAGES_FOR_ANALYSIS': data['minMessages'],
            'MAX_MESSAGES_FOR_ANALYSIS': data['maxMessages']
        }

        if update_config(new_config):
            # Reinitialize Azure OpenAI client with new settings
            global endpoint, deployment, subscription_key
            endpoint = os.getenv("ENDPOINT_URL", new_config['ENDPOINT_URL'])
            deployment = os.getenv("DEPLOYMENT_NAME", new_config['DEPLOYMENT_NAME'])
            subscription_key = os.getenv("AZURE_OPENAI_API_KEY", new_config['AZURE_OPENAI_API_KEY'])

            global client
            client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=subscription_key,
                api_version=new_config['API_VERSION'],
            )

            # Update global variables
            global fix_time, MIN_MESSAGES_FOR_ANALYSIS, MAX_MESSAGES_FOR_ANALYSIS
            fix_time = new_config['fix_time']
            MIN_MESSAGES_FOR_ANALYSIS = new_config['MIN_MESSAGES_FOR_ANALYSIS']
            MAX_MESSAGES_FOR_ANALYSIS = new_config['MAX_MESSAGES_FOR_ANALYSIS']

            return jsonify({
                'success': True,
                'message': 'Configuration updated successfully'
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to update configuration'
            }), 500
    except Exception as e:
        logger.error(f"Error updating configuration: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# New API endpoints for control panel
@app.route('/api/auto-reply', methods=['POST'])
def toggle_auto_reply():
    """切换自动回复功能"""
    global auto_reply_enabled
    try:
        data = request.json
        auto_reply_enabled = data.get('enabled', False)
        return jsonify({
            'success': True,
            'enabled': auto_reply_enabled
        })
    except Exception as e:
        logger.error(f"切换自动回复失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/learning-user', methods=['POST'])
def set_learning_user():
    """设置要学习的用户"""
    global current_learning_user, learning_users
    try:
        data = request.json
        user_id = data.get('userId')

        if user_id:
            learning_users = {user_id}
            current_learning_user = user_id
        else:
            learning_users = set()
            current_learning_user = None

        return jsonify({
            'success': True,
            'userId': user_id
        })
    except Exception as e:
        logger.error(f"设置学习用户失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/friends')
def get_friends():
    """获取微信好友列表"""
    try:
        friends = itchat.get_friends(update=True)
        friend_list = [{
            'id': friend['UserName'],
            'nickname': friend['NickName'],
            'remarkName': friend.get('RemarkName', '')
        } for friend in friends]
        return jsonify({
            'success': True,
            'friends': friend_list
        })
    except Exception as e:
        logger.error(f"获取好友列表失败: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/status')
def get_status():
    """获取当前bot状态"""
    return jsonify({
        'auto_reply': auto_reply_enabled,
        'learning_user': current_learning_user,
        'total_messages': Session().query(ChatMessage).count(),
        'active_users': len(set(msg.sender_name for msg in Session().query(ChatMessage).all()))
    })


# Modified message handler with auto-reply control
@itchat.msg_register([TEXT])
def handle_text(msg):
    """使用自动回复控件处理文本消息"""
    try:
        username = msg['FromUserName']
        content = msg['Text']

        # 始终从学习用户中学习风格
        if username in learning_users:
            update_user_style(username, content)
            logger.info(f"学习用户 {username} 的消息风格")
            if not auto_reply_enabled:
                return None

        # 如果禁用了自动回复，则跳过处理
        if not auto_reply_enabled and username not in learning_users:
            logger.debug("自动回复已关闭，忽略消息")
            return None

        if username not in message_buffer:
            message_buffer[username] = []
            buffer_locks[username] = Lock()
            buffer_timers[username] = None

        with buffer_locks[username]:
            message_buffer[username].append(content)

        sender = itchat.search_friends(userName=username)
        sender_name = sender['NickName'] if sender else username
        logger.info(f"收到消息 - 发送者: {sender_name}, 内容: {content}")

        schedule_processing(username)
        return None

    except Exception as e:
        logger.error(f"处理消息失败: {str(e)}")
        return "gg"


def main():
    """修改主功能与web界面集成"""
    try:
        # 加载保存的用户样式
        load_user_styles()

        # 启动Flask线程
        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()
        logger.info("监控服务器已启动")

        # 寄存器清理函数
        atexit.register(cleanup_resources)
        atexit.register(save_user_styles)

        # 微信登录
        retry_count = 0
        max_retries = 3

        while retry_count < max_retries:
            try:
                if login_wechat():
                    logger.info("微信机器人启动成功")

                    # 运行主循环
                    itchat.run(debug=True)
                    break
                else:
                    retry_count += 1
                    if retry_count < max_retries:
                        logger.info(f"等待 10 秒后进行第 {retry_count + 1} 次重试")
                        time.sleep(10)
            except Exception as e:
                logger.error(f"运行出错: {str(e)}")
                retry_count += 1
                if retry_count < max_retries:
                    logger.info(f"等待 10 秒后进行第 {retry_count + 1} 次重试")
                    time.sleep(10)

        if retry_count >= max_retries:
            logger.error("多次尝试登录失败，程序退出")

    except Exception as e:
        logger.error(f"程序运行错误: {str(e)}")
    finally:
        cleanup_resources()
        save_user_styles()
        logger.info("程序退出")


if __name__ == '__main__':
    try:
        # 检查itchat-uos版本
        if not hasattr(itchat, '__version__') or itchat.__version__ < '1.5.0':
            logger.warning("建议更新 itchat-uos 到最新版本")
        main()
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
    except Exception as e:
        logger.error(f"程序异常退出: {str(e)}")

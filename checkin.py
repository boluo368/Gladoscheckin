# 导入所需依赖库
import requests       # 用于发送HTTP请求
import json           # 用于解析JSON格式的响应数据
import os             # 用于读取系统环境变量
import logging        # 用于日志输出
import datetime       # 用于时间处理（时区、格式化）
from typing import Dict, List, Optional, Tuple  # 用于类型注解，提升代码可读性
from pypushdeer import PushDeer  # PushDeer推送库，用于发送手机通知

# -------------------------- 全局工具函数 --------------------------
def beijing_time_converter(timestamp):
    """
    日志时间转换函数：将UTC时间戳转换为北京时间
    :param timestamp: 系统UTC时间戳
    :return: 北京时间的时间元组
    """
    # 将时间戳转换为UTC时区的datetime对象
    utc_dt = datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc)
    # 定义北京时区（东八区）
    beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
    # 转换为北京时间
    beijing_dt = utc_dt.astimezone(beijing_tz)
    return beijing_dt.timetuple()

# 定义北京时区常量（全局复用）
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8))

# -------------------------- 日志配置 --------------------------
# 配置日志输出格式：时间 - 日志级别 - 日志内容（时间已通过上面的函数转为北京时间）
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 获取根日志器，确保所有日志处理器都使用北京时间
root_logger = logging.getLogger()
for handler in root_logger.handlers:
    if hasattr(handler, 'formatter') and handler.formatter is not None:
        handler.formatter.converter = beijing_time_converter

# 创建当前模块的日志器（后续代码中使用logger输出日志）
logger = logging.getLogger(__name__)

# -------------------------- 全局配置常量 --------------------------
# 环境变量名称（对应GitHub Actions/本地环境配置）
ENV_PUSH_KEY = "PUSHDEER_SENDKEY"    # PushDeer推送密钥
ENV_COOKIES = "GLADOS_COOKIES"       # GLaDOS账号Cookie（多账号用&分隔）
ENV_EXCHANGE_PLAN = "GLADOS_EXCHANGE_PLAN"  # 积分兑换计划（plan100/200/500）

# API接口地址（GLaDOS签到相关）
CHECKIN_URL = "https://glados.cloud/api/user/checkin"  # 签到接口
STATUS_URL = "https://glados.cloud/api/user/status"    # 账号状态接口（获取邮箱、剩余天数）
POINTS_URL = "https://glados.cloud/api/user/points"    # 积分查询接口
EXCHANGE_URL = "https://glados.cloud/api/user/exchange" # 积分兑换接口

# 签到接口POST请求参数
CHECKIN_DATA = {"token": "glados.cloud"} 

# HTTP请求头模板（模拟浏览器请求，避免被拦截）
HEADERS_TEMPLATE = {
    'referer': 'https://glados.cloud/console/checkin',  # 来源页
    'origin': "https://glados.cloud",                   # 域名
    'user-agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/102.0.0.0 Safari/537.36",
    'content-type': 'application/json;charset=UTF-8'    # 请求体格式
}

# 积分兑换计划对应的积分要求（key=计划名称，value=所需积分）
EXCHANGE_POINTS = {"plan100": 100, "plan200": 200, "plan500": 500} 
# 兑换档位配置（用于推送展示，格式：(所需积分, 兑换天数)）
EXCHANGE_GRADES = [(100, 10), (200, 30), (500, 100)]

# -------------------------- 核心函数 --------------------------
def load_config() -> Tuple[str, List[str], str]:
    """
    加载环境变量配置
    :return: 元组（推送密钥, Cookie列表, 兑换计划）
    """
    # 读取PushDeer推送密钥
    push_key_env = os.environ.get(ENV_PUSH_KEY)
    # 读取GLaDOS Cookie（多账号用&分隔）
    raw_cookies_env = os.environ.get(ENV_COOKIES)
    # 读取积分兑换计划
    exchange_plan_env = os.environ.get(ENV_EXCHANGE_PLAN)

    # 处理推送密钥（未设置则为空）
    if not push_key_env:
        logger.warning(f"环境变量 '{ENV_PUSH_KEY}' 未设置，将跳过推送。")
        push_key = ''
    else:
        push_key = push_key_env

    # 处理Cookie（拆分多账号、去空）
    if not raw_cookies_env:
        logger.warning(f"环境变量 '{ENV_COOKIES}' 未设置，无账号可签到。")
        cookies_list = []
    else:
        # 按&拆分多账号Cookie，去除首尾空格，过滤空值
        cookies_list = [cookie.strip() for cookie in raw_cookies_env.split('&') if cookie.strip()]
        if not cookies_list:
            raise ValueError(f"环境变量 '{ENV_COOKIES}' 已设置，但无有效Cookie（请检查格式，多账号用&分隔）。")

    # 处理兑换计划（默认使用plan500）
    if not exchange_plan_env:
        logger.warning(f"环境变量 '{ENV_EXCHANGE_PLAN}' 未设置，使用默认计划 'plan500'。")
        exchange_plan = "plan500"
    else: 
        # 验证兑换计划是否有效
        if exchange_plan_env in EXCHANGE_POINTS:
             exchange_plan = exchange_plan_env
             logger.info(f"使用指定兑换计划: {exchange_plan}")
        else:
            logger.warning(f"兑换计划 '{exchange_plan_env}' 无效，使用默认 'plan500'。")
            exchange_plan = "plan500"

    # 输出配置加载日志
    logger.info(f"共加载 {len(cookies_list)} 个账号Cookie。")
    logger.info(f"推送密钥状态: {'已设置' if push_key_env else '未设置'}。")
    logger.info(f"当前兑换计划: {exchange_plan}。")

    return push_key, cookies_list, exchange_plan


def make_request(url: str, method: str, headers: Dict[str, str], data: Optional[Dict] = None, cookies: str = "") -> Optional[requests.Response]:
    """
    （原始版）发送HTTP请求（POST/GET），添加Cookie到请求头
    :param url: 请求地址
    :param method: 请求方法（POST/GET）
    :param headers: 请求头模板
    :param data: POST请求体（可选）
    :param cookies: 账号Cookie（可选）
    :return: 请求响应对象（失败返回None）
    """
    # 复制请求头模板，避免修改原模板
    session_headers = headers.copy()
    # 添加Cookie到请求头（GLaDOS鉴权用）
    session_headers['cookie'] = cookies

    try:
        # 发送POST请求
        if method.upper() == 'POST':
            response = requests.post(url, headers=session_headers, data=json.dumps(data))
        # 发送GET请求
        elif method.upper() == 'GET':
            response = requests.get(url, headers=session_headers)
        # 不支持的请求方法
        else:
            logger.error(f"不支持的HTTP方法: {method}")
            return None

        # 检查响应状态码（非200系列则视为失败）
        if not response.ok:
            logger.warning(f"请求 {url} 失败，状态码: {response.status_code}，响应内容: {response.text}")
            return None
        return response
    except requests.exceptions.RequestException as e:
        # 捕获网络异常（如超时、连接失败）
        logger.error(f"请求 {url} 发生网络错误: {e}")
        return None


def checkin_and_process(cookie: str, exchange_plan: str) -> Tuple[str, str, str, str, str, str]:
    """
    执行单个账号的签到、积分查询、兑换逻辑
    :param cookie: 单个账号的Cookie
    :param exchange_plan: 积分兑换计划
    :return: 元组（签到状态, 本次获得积分, 剩余天数, 总积分, 兑换状态, 账号邮箱）
    """
    # 初始化返回值（默认失败状态）
    status_msg = "签到请求失败"       # 签到状态
    points_gained = "0"               # 本次签到获得积分
    remaining_days = "获取剩余天数失败" # 账号剩余使用天数
    remaining_points = "获取剩余积分失败" # 账号总积分
    exchange_msg = "兑换跳过或失败"    # 兑换状态
    user_email = "获取邮箱失败"        # 账号绑定邮箱

    # 第一步：发送签到请求
    checkin_response = make_request(CHECKIN_URL, 'POST', HEADERS_TEMPLATE, CHECKIN_DATA, cookies=cookie)
    if not checkin_response:
        return status_msg, points_gained, remaining_days, remaining_points, exchange_msg, user_email

    # 解析签到响应（JSON格式）
    try:
        checkin_data = checkin_response.json()
        response_message = checkin_data.get('message', '无消息字段')  # 签到接口返回的提示信息
        points_gained = str(checkin_data.get('points', 0))           # 本次获得积分

        # 解析签到结果
        response_code = checkin_data.get('code', -1)
        if response_code == 0:
            # code=0 均为请求成功，区分首次签到和重复签到
            got_points = checkin_data.get('points', 0)
            if got_points > 0:
                status_msg = f"签到成功，获得 {points_gained} 积分"
            else:
                status_msg = "今日已签到，明日再来"
                points_gained = "0"
        else:
            status_msg = f"签到失败：{response_message}"
            points_gained = "0"
    except json.JSONDecodeError:
        logger.error(f"解析签到响应失败: {checkin_response.text}")
        return status_msg, points_gained, remaining_days, remaining_points, exchange_msg, user_email

    # 第二步：查询账号状态（获取邮箱、剩余天数）
    status_response = make_request(STATUS_URL, 'GET', HEADERS_TEMPLATE, cookies=cookie)
    if status_response:
        try:
            status_data = status_response.json()
            # 提取账号邮箱（核心新增字段）
            user_email = status_data.get('data', {}).get('email', '获取邮箱失败')
            # 提取剩余天数（浮点转整数）
            left_days_float = status_data.get('data', {}).get('leftDays', None)
            if left_days_float is not None:
                remaining_days = f"{int(float(left_days_float))} 天"
            else:
                remaining_days = "获取剩余天数失败 (响应结构异常)"
        except json.JSONDecodeError:
            logger.error(f"解析状态响应失败: {status_response.text}")
            remaining_days = "获取剩余天数失败 (JSON解析错误)"
        except (ValueError, TypeError):
            logger.error(f"解析剩余天数出错: {status_data.get('data', {}).get('leftDays', 'unknown')}")
            remaining_days = "获取剩余天数失败 (数值转换错误)"
    else:
        remaining_days = "获取剩余天数失败 (HTTP请求失败)"

    # 第三步：查询账号积分
    points_response = make_request(POINTS_URL, 'GET', HEADERS_TEMPLATE, cookies=cookie)
    points_data = {}  # 存储积分响应数据（后续兑换逻辑复用）
    if points_response:
        try:
            points_data = points_response.json()
            points_float = points_data.get('points', None)
            if points_float is not None:
                remaining_points = f"{int(float(points_float))} 积分"
            else:
                remaining_points = "获取剩余积分失败 (响应结构异常)"
        except json.JSONDecodeError:
            logger.error(f"解析积分响应失败: {points_response.text}")
            remaining_points = "获取剩余积分失败 (JSON解析错误)"
        except (ValueError, TypeError):
            logger.error(f"解析剩余积分出错: {points_data.get('points', 'unknown')}")
            remaining_points = "获取剩余积分失败 (数值转换错误)"
    else:
        remaining_points = "获取剩余积分失败 (HTTP请求失败)"

    # 第四步：积分兑换逻辑（仅当积分足够时执行）
    current_points_numeric = 0  # 积分数值（用于判断是否可兑换）
    try:
        current_points_numeric = int(float(points_data.get('points', 0)))
    except (ValueError, TypeError):
        logger.warning(f"无法解析积分数值，跳过兑换判断: {remaining_points}")

    # 获取当前兑换计划所需积分
    required_points = EXCHANGE_POINTS.get(exchange_plan, 500) 
    if current_points_numeric >= required_points:
        logger.info(f"积分充足，执行兑换: {exchange_plan} (需{required_points}分，当前{current_points_numeric}分)")
        # 发送兑换请求
        exchange_response = make_request(EXCHANGE_URL, 'POST', HEADERS_TEMPLATE, {"planType": exchange_plan}, cookies=cookie)
        if exchange_response:
            try:
                exchange_data = exchange_response.json()
                code = exchange_data.get('code', -1)  # 兑换接口返回码（0=成功）
                if code == 0:
                    exchange_msg = f"兑换成功：{exchange_plan}"
                else:
                    detailed_msg = exchange_data.get('message', "未知错误")
                    exchange_msg = f"兑换失败: {exchange_plan}, 错误码: {code}, 详情: {detailed_msg}"
            except json.JSONDecodeError:
                logger.error(f"解析兑换响应失败: {exchange_response.text}")
                exchange_msg = f"兑换响应解析失败: {exchange_plan}"
        else:
            exchange_msg = f"兑换请求失败：{exchange_plan}"
    else:
        logger.info(f"积分不足，跳过兑换: 需{required_points}分，当前{current_points_numeric}分")
        exchange_msg = f"积分不足，未兑换: {exchange_plan}"

    # 返回所有结果（新增邮箱字段）
    return status_msg, points_gained, remaining_days, remaining_points, exchange_msg, user_email


def format_push_content(results: List[Dict[str, str]]) -> Tuple[str, str]:
    """
    格式化推送内容（按指定样式生成标题和正文）
    :param results: 所有账号的签到结果列表（字典格式）
    :return: 元组（推送标题, 推送正文）
    """
    # 统计签到状态数量
    success_count = sum(1 for r in results if "签到成功" in r['status'])  # 成功数
    repeat_count = sum(1 for r in results if "重复签到" in r['status'])    # 重复数
    fail_count = sum(1 for r in results if "失败" in r['status'])         # 失败数
    # 生成推送标题
    title = f'GLaDOS签到结果: 成功{success_count} | 重复{repeat_count} | 失败{fail_count}'

    # 构建推送正文
    content_lines = []
    # 遍历每个账号的结果
    for i, res in enumerate(results, 1):
        content_lines.append(f"【账号 {i}】")
        # 账号邮箱（2个空格缩进）
        content_lines.append(f"  邮箱：{res['email']}")
        
        # 解析当前积分（提取数值，用于兑换判断）
        try:
            current_points = int(res['points_total'].split(' ')[0]) if res['points_total'] != "获取剩余积分失败" else 0
        except (IndexError, ValueError):
            current_points = 0
            res['points_total'] = "0 积分"
        
        # 解析本次获得积分（带+号）
        try:
            points_gained = f"+{int(res['points'])}" if res['points'] != "0" else "+0"
        except ValueError:
            points_gained = "+0"
        
        # 剩余天数/签到结果（2个空格缩进）
        content_lines.append(f"  当前积分：{current_points} ({points_gained})")
        content_lines.append(f"  剩余天数：{res['days']}")
        content_lines.append(f"  签到结果：{res['status']}")
        # 兑换选项标题（2个空格缩进）
        content_lines.append(f"  兑换选项：")
        
        # 遍历兑换档位，生成✅/❌标识（4个空格缩进）
        for need_p, get_d in EXCHANGE_GRADES:
            if current_points >= need_p:
                content_lines.append(f"    ✅ {need_p}分 → {get_d}天 (可兑换)")
            else:
                lack_p = need_p - current_points
                content_lines.append(f"    ❌ {need_p}分 → {get_d}天 (差{lack_p}分)")
        
        # 多账号之间空一行分隔
        if i < len(results):
            content_lines.append("")

    # 新增推送时间（北京时间，精确到秒）
    push_time = datetime.datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
    content_lines.append("")
    content_lines.append(f"📅 推送时间（北京时间）：{push_time}")
    
    # 拼接所有行，生成最终正文
    content = "\n".join(content_lines)
    return title, content


def main():
    """
    主函数：程序入口，执行完整的签到流程
    """
    try:
        # 第一步：加载环境配置
        push_key, cookies_list, exchange_plan = load_config()

        # 无有效Cookie时直接退出
        if not cookies_list:
            logger.error("未找到有效Cookie，程序退出。")
            title, content = "GLaDOS签到结果", "# 未找到有效的Cookie，请检查环境变量配置！"
        else:
            # 第二步：遍历所有账号执行签到
            results = []  # 存储所有账号的签到结果
            for idx, cookie in enumerate(cookies_list, 1):
                logger.info(f"开始处理第 {idx} 个账号...")
                # 执行签到+积分+兑换逻辑（接收新增的邮箱返回值）
                status, points, days, points_total, exchange, email = checkin_and_process(cookie, exchange_plan)
                # 存储当前账号结果
                results.append({
                    'status': status,          # 签到状态
                    'points': points,          # 本次获得积分
                    'days': days,              # 剩余天数
                    'points_total': points_total,  # 总积分
                    'exchange': exchange,      # 兑换状态
                    'email': email             # 账号邮箱
                })

            # 第三步：格式化推送内容
            title, content = format_push_content(results)
            logger.info(f"推送标题: {title}")
            logger.info(f"推送内容:\n{content}")

    except Exception as e:
        # 捕获所有未预期的异常，保证程序不崩溃
        logger.error(f"程序执行出错: {e}", exc_info=True)  # 输出完整报错堆栈
        # 生成异常推送内容（带时间）
        push_time = datetime.datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        title = "GLaDOS签到结果"
        content = f"❌ 脚本执行出错：{str(e)}\n\n📅 推送时间（北京时间）：{push_time}"

    # 第四步：发送PushDeer推送（仅当配置了推送密钥时）
    if not push_key:
        logger.info("未配置推送密钥，跳过推送。")
    else:
        try:
            # 初始化PushDeer
            pushdeer = PushDeer(pushkey=push_key)
            # 发送文本推送（title=标题，desp=正文）
            pushdeer.send_text(title, desp=content)
            logger.info("推送通知发送成功。")
        except Exception as e:
            logger.error(f"推送发送失败: {e}")

# 程序入口（仅当直接运行该脚本时执行）
if __name__ == '__main__':
    main()

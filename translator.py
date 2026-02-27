#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
有道翻译API核心模块
提供文本翻译功能
"""

import hashlib
import time
import uuid
import requests
import json


class YoudaoTranslator:
    """有道翻译API封装类"""
    
    API_URL = "https://openapi.youdao.com/api"
    
    # 支持的语言列表
    LANGUAGES = {
        "自动检测": "auto",
        "中文简体": "zh-CHS",
        "中文繁体": "zh-CHT",
        "英语": "en",
        "日语": "ja",
        "韩语": "ko",
        "法语": "fr",
        "德语": "de",
        "俄语": "ru",
        "西班牙语": "es",
        "葡萄牙语": "pt",
        "意大利语": "it",
        "阿拉伯语": "ar",
        "泰语": "th",
        "越南语": "vi",
        "印尼语": "id",
        "马来语": "ms",
        "荷兰语": "nl",
        "波兰语": "pl",
        "土耳其语": "tr",
    }
    
    def __init__(self, app_key: str, app_secret: str):
        """
        初始化翻译器
        
        Args:
            app_key: 有道智云应用ID
            app_secret: 有道智云应用密钥
        """
        self.app_key = app_key
        self.app_secret = app_secret
    
    def _generate_sign(self, query: str, salt: str, curtime: str) -> str:
        """
        生成API签名
        
        Args:
            query: 待翻译文本
            salt: 随机字符串
            curtime: 当前时间戳
            
        Returns:
            SHA256签名字符串
        """
        # 计算input: q前10个字符 + q长度 + q后10个字符 (当q长度>20)
        # 或 input = q字符串 (当q长度<=20)
        if len(query) <= 20:
            input_str = query
        else:
            input_str = query[:10] + str(len(query)) + query[-10:]
        
        # 签名 = sha256(应用ID+input+salt+curtime+应用密钥)
        sign_str = self.app_key + input_str + salt + curtime + self.app_secret
        return hashlib.sha256(sign_str.encode('utf-8')).hexdigest()
    
    def translate(self, text: str, from_lang: str = "auto", to_lang: str = "en") -> dict:
        """
        翻译文本
        
        Args:
            text: 待翻译文本
            from_lang: 源语言代码，默认自动检测
            to_lang: 目标语言代码，默认英语
            
        Returns:
            翻译结果字典，包含:
            - success: 是否成功
            - translation: 翻译结果
            - error: 错误信息(如果失败)
            - query: 原文
        """
        if not text.strip():
            return {
                "success": False,
                "translation": "",
                "error": "翻译文本不能为空",
                "query": text
            }
        
        # 生成必要参数
        salt = str(uuid.uuid4())
        curtime = str(int(time.time()))
        sign = self._generate_sign(text, salt, curtime)
        
        # 构建请求参数
        params = {
            "q": text,
            "from": from_lang,
            "to": to_lang,
            "appKey": self.app_key,
            "salt": salt,
            "sign": sign,
            "signType": "v3",
            "curtime": curtime,
        }
        
        try:
            response = requests.post(self.API_URL, data=params, timeout=10)
            result = response.json()
            
            error_code = result.get("errorCode", "")
            
            if error_code == "0":
                translation_list = result.get("translation", [])
                translation = "\n".join(translation_list) if translation_list else ""
                return {
                    "success": True,
                    "translation": translation,
                    "error": "",
                    "query": result.get("query", text),
                    "speakUrl": result.get("speakUrl", ""),
                    "tSpeakUrl": result.get("tSpeakUrl", ""),
                }
            else:
                error_msg = self._get_error_message(error_code)
                return {
                    "success": False,
                    "translation": "",
                    "error": f"错误码 {error_code}: {error_msg}",
                    "query": text
                }
                
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "translation": "",
                "error": "请求超时，请检查网络连接",
                "query": text
            }
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "translation": "",
                "error": f"网络请求失败: {str(e)}",
                "query": text
            }
        except json.JSONDecodeError:
            return {
                "success": False,
                "translation": "",
                "error": "服务器返回数据格式错误",
                "query": text
            }
    
    def _get_error_message(self, error_code: str) -> str:
        """根据错误码返回错误信息"""
        error_messages = {
            "101": "缺少必填参数",
            "102": "不支持的语言类型",
            "103": "翻译文本过长",
            "108": "应用ID无效",
            "110": "无相关服务的有效应用",
            "111": "开发者账号无效",
            "202": "签名检验失败，请检查appKey和密钥",
            "206": "时间戳无效",
            "207": "重放请求",
            "401": "账户已欠费",
            "411": "访问频率受限",
        }
        return error_messages.get(error_code, "未知错误")


def load_config(config_path: str = None) -> dict:
    """
    加载配置文件
    
    Args:
        config_path: 配置文件路径，默认为当前目录下的config.json
        
    Returns:
        配置字典
    """
    if config_path is None:
        import os
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


class YoudaoOCR:
    """有道智云OCR API封装类"""
    
    OCR_API_URL = "https://openapi.youdao.com/ocrapi"
    
    # OCR支持的语言（常用）
    OCR_LANGUAGES = {
        "自动识别": "auto",
        "中文简体": "zh-CHS",
        "中文繁体": "zh-CHT",
        "英语": "en",
        "日语": "ja",
        "韩语": "ko",
        "法语": "fr",
        "德语": "de",
        "俄语": "ru",
        "西班牙语": "es",
        "葡萄牙语": "pt",
        "意大利语": "it",
        "阿拉伯语": "ar",
        "泰语": "th",
        "越南语": "vi",
    }
    
    def __init__(self, app_key: str, app_secret: str):
        """
        初始化OCR识别器
        
        Args:
            app_key: 有道智云应用ID
            app_secret: 有道智云应用密钥
        """
        self.app_key = app_key
        self.app_secret = app_secret
    
    def _generate_sign(self, img_base64: str, salt: str, curtime: str) -> str:
        """
        生成API签名
        
        Args:
            img_base64: Base64编码的图片
            salt: 随机字符串
            curtime: 当前时间戳
            
        Returns:
            SHA256签名字符串
        """
        # 计算input: img前10个字符 + img长度 + img后10个字符 (当img长度>20)
        # 或 input = img字符串 (当img长度<=20)
        if len(img_base64) <= 20:
            input_str = img_base64
        else:
            input_str = img_base64[:10] + str(len(img_base64)) + img_base64[-10:]
        
        # 签名 = sha256(应用ID+input+salt+curtime+应用密钥)
        sign_str = self.app_key + input_str + salt + curtime + self.app_secret
        return hashlib.sha256(sign_str.encode('utf-8')).hexdigest()
    
    def recognize(self, image_data: bytes, lang: str = "auto") -> dict:
        """
        识别图片中的文字
        
        Args:
            image_data: 图片二进制数据
            lang: 语言代码，默认自动识别
            
        Returns:
            识别结果字典，包含:
            - success: 是否成功
            - text: 识别的文字
            - error: 错误信息(如果失败)
            - regions: 详细区域信息
        """
        import base64
        
        # Base64编码图片
        img_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # 生成必要参数
        salt = str(uuid.uuid4())
        curtime = str(int(time.time()))
        sign = self._generate_sign(img_base64, salt, curtime)
        
        # 构建请求参数
        params = {
            "img": img_base64,
            "langType": lang,
            "detectType": "10012",  # 按行识别
            "imageType": "1",  # Base64类型
            "appKey": self.app_key,
            "salt": salt,
            "sign": sign,
            "signType": "v3",
            "curtime": curtime,
            "docType": "json",
        }
        
        try:
            response = requests.post(self.OCR_API_URL, data=params, timeout=30)
            result = response.json()
            
            error_code = result.get("errorCode", "")
            
            if error_code == "0":
                # 提取文字
                text_lines = []
                regions = result.get("Result", {}).get("regions", [])
                
                for region in regions:
                    for line in region.get("lines", []):
                        line_text = line.get("text", "")
                        if line_text:
                            text_lines.append(line_text)
                
                full_text = "\n".join(text_lines)
                
                return {
                    "success": True,
                    "text": full_text,
                    "error": "",
                    "regions": regions,
                }
            else:
                error_msg = self._get_error_message(error_code)
                return {
                    "success": False,
                    "text": "",
                    "error": f"错误码 {error_code}: {error_msg}",
                    "regions": [],
                }
                
        except requests.exceptions.Timeout:
            return {
                "success": False,
                "text": "",
                "error": "请求超时，请检查网络连接",
                "regions": [],
            }
        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "text": "",
                "error": f"网络请求失败: {str(e)}",
                "regions": [],
            }
        except json.JSONDecodeError:
            return {
                "success": False,
                "text": "",
                "error": "服务器返回数据格式错误",
                "regions": [],
            }
    
    def _get_error_message(self, error_code: str) -> str:
        """根据错误码返回错误信息"""
        error_messages = {
            "101": "缺少必填参数",
            "102": "不支持的语言类型",
            "108": "应用ID无效",
            "110": "无相关服务的有效应用",
            "111": "开发者账号无效",
            "202": "签名检验失败，请检查appKey和密钥",
            "206": "时间戳无效",
            "207": "重放请求",
            "401": "账户已欠费",
            "411": "访问频率受限",
            "1001": "无效的OCR类型",
            "1002": "不支持的OCR图片类型",
            "1003": "不支持的OCR语言类型",
            "1004": "识别图片过大",
            "1006": "图片不能为空",
            "1201": "图片base64解密失败",
            "1301": "OCR识别失败",
        }
        return error_messages.get(error_code, "未知错误")


if __name__ == "__main__":
    # 测试代码
    config = load_config()
    
    if not config.get("app_key") or config.get("app_key") == "请在这里填写你的appKey":
        print("请先在 config.json 中填写你的 appKey 和 app_secret")
        exit(1)
    
    translator = YoudaoTranslator(config["app_key"], config["app_secret"])
    
    # 测试翻译
    result = translator.translate("Hello, world!", "en", "zh-CHS")
    print(json.dumps(result, ensure_ascii=False, indent=2))
import requests
import traceback
import json

loaded = False


def singleton(cls):
    instances = {}

    def getinstance(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return getinstance


@singleton
class Logger:
    def __init__(self, credentials_path="credentials/logger.json"):

        with open(credentials_path, "r") as f:
            logger_config = json.load(f)
        print("Setting telegram logger token")
        self.telegram_apikey = logger_config.get("telegram_apikey", None)
        if self.telegram_apikey is None:
            raise ValueError("Telegram API key is not set in the logger.json file, logs will not be sent to Telegram.")
        self.logs_user_id = logger_config.get("user_id", None)
        if self.logs_user_id is None:
            raise ValueError("LOGS_USER_ID is not set in the logger.json file, logs will not be sent to Telegram.")
        self.name = logger_config.get("project_name", None)
        if self.name is None:
            print("WARNING: Project name is not set in the logger.json file, using default name 'Test Logger'")
            self.name = "Test Logger"

    @staticmethod
    def escape_markdown(text):
        escape_chars = ['_', '*', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in escape_chars:
            text = text.replace(char, f'\\{char}')
        return text

    def log(self, text, markdown: bool = True):
        url = f"https://api.telegram.org/bot{self.telegram_apikey}/sendMessage"
        text = f"From {self.name}:\n\n" + str(text)
        text = self.escape_markdown(text)
        if self.logs_user_id is None:
            print("\n\nThis message was not sent to Telegram because the ID_LOGS is not set in the logger.json file")
            return
        params = {
            "chat_id": self.logs_user_id,
            "text": text,
        }
        if markdown: params["parse_mode"] = "MarkdownV2"
        resp = requests.post(url, params=params)
        if resp.status_code != 200:
            print(f"Failed to send log to Telegram: {resp.status_code} {resp.text}")

    def err(self, error: Exception, additional_text: str = ""):
        traceback_str = ''.join(traceback.format_exception(
            type(error),
            error,
            error.__traceback__)
        )
        text = f"""{additional_text}\n```python\n{traceback_str}```"""
        self.log(text)

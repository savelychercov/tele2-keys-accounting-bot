import json
from aiogram.enums import ContentType
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, Message, ErrorEvent)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from datetime import datetime, timedelta
from requests.exceptions import ConnectionError
import asyncio
import sheets
import logger
import os
import sys
from typing import List, Dict, Union


# region Constants and Configuration

class Config:
    REQUEST_DELAY = 60 * 60  # 1 hour
    REMINDER_DELAY = 60 * 60 * 24  # 24 hours
    MESSAGE_CHUNK_SIZE = 2000  # Telegram message length limit


def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


# endregion

# region Initialization

logger = logger.Logger()
print("Setting bot token")
with open(resource_path(os.path.join("credentials", "telegram_bot.json")), "r") as f:
    API_TOKEN = json.load(f)["telegram_apikey"]
dp = Dispatcher(storage=MemoryStorage())
bot: Bot = Bot(API_TOKEN)
print("Bot connected")

print("Connecting to worksheets")
keys_accounting_table = sheets.KeysAccountingTable()
keys_table = sheets.KeysTable()
emp_table = sheets.EmployeesTable()
print("Worksheets connected")


async def main():
    dp.errors.register(callback=on_error)
    await dp.start_polling(bot)


# endregion

# region Utils


class UserNotFoundError(Exception):
    pass


class BotUtils:
    @staticmethod
    def escape_markdown(text: str) -> str:
        escape_chars = ['_', '*', '[', '`']
        for char in escape_chars:
            text = text.replace(char, f'\\{char}')
        return text

    @staticmethod
    def phone_format(phone: Union[str, int]) -> str:
        phone = str(phone)
        digits = ''.join(filter(str.isdigit, phone))
        if digits.startswith('8'):
            digits = '7' + digits[1:]
        elif not digits.startswith('7'):
            digits = '7' + digits
        digits = digits[:11]
        return f'+{digits}'

    @staticmethod
    async def remove_key_after_delay(key: str, dictionary: Dict[str, int], delay: int = 600) -> None:
        await asyncio.sleep(delay)
        if key in dictionary:
            try:
                await bot.send_message(chat_id=dictionary[key],
                                       text=f"Время запроса на ключ {key} истекло.")
            except Exception as e:
                print("Не удалось отправить сообщение пользователю:\n", e)
            del dictionary[key]

    @staticmethod
    async def check_permission(user_id: str, required_role: str) -> bool:
        user_id = str(user_id)
        employees = emp_table.get_all_employees()
        emp = next((emp for emp in employees if emp.telegram == user_id), None)
        if not emp:
            raise UserNotFoundError("User not found")
        return emp is not None and required_role in emp.roles or "admin" in emp.roles

    @staticmethod
    async def is_registered(user_id: str) -> bool:
        user_id = str(user_id)
        employees = emp_table.get_all_employees()
        return any(emp.telegram == user_id for emp in employees)

    @staticmethod
    def make_keyboard(
            buttons: List[List[Union[str, Dict]]],
            inline: bool = False
    ) -> Union[ReplyKeyboardMarkup, InlineKeyboardMarkup]:
        if inline:
            keyboard = []
            for row in buttons:
                keyboard_row = []
                for btn in row:
                    if isinstance(btn, dict):
                        # For inline buttons with special parameters
                        keyboard_row.append(InlineKeyboardButton(**btn))
                    else:
                        # For simple inline buttons with just text
                        keyboard_row.append(InlineKeyboardButton(text=btn))
                keyboard.append(keyboard_row)
            return InlineKeyboardMarkup(inline_keyboard=keyboard)
        else:
            keyboard = []
            for row in buttons:
                keyboard_row = []
                for btn in row:
                    if isinstance(btn, dict):
                        # For special reply keyboard buttons (like request_contact)
                        keyboard_row.append(KeyboardButton(**btn))
                    else:
                        # For simple reply keyboard buttons
                        keyboard_row.append(KeyboardButton(text=btn))
                keyboard.append(keyboard_row)
            return ReplyKeyboardMarkup(
                keyboard=keyboard,
                resize_keyboard=True,
                one_time_keyboard=True
            )


class KeyCommandMixin:
    """Mixin for commands that work with keys"""

    @staticmethod
    async def find_similar_keys(search_term: str) -> List[str]:
        key_names = {key.key_name for key in keys_table.get_all_keys()}
        return sheets.find_similar(search_term, key_names)

    @staticmethod
    async def find_similar_employees(search_term: str) -> List[str]:
        entries = keys_accounting_table.get_all_entries()
        emp_obj = emp_table.get_all_employees()
        emp_names = (
                {f"{entry.emp_firstname} {entry.emp_lastname}" for entry in entries} |
                {f"{emp.first_name} {emp.last_name}" for emp in emp_obj}
        )
        similarities = set()
        for name in sheets.permute(search_term):
            similarities.update(sheets.find_similar(name, emp_names))
        return list(similarities)

    @staticmethod
    async def get_key_state(key_name: str) -> str:
        entries = keys_accounting_table.get_all_entries()
        key_entries = [entry for entry in entries if entry.key_name == key_name]

        if not key_entries:
            key = keys_table.get_by_name(key_name)
            if not key:
                return "По этому ключу нет записей в истории и в таблице ключей"
            return (
                f"*Ключ*: `{key_name}`\n"
                f"*  Состояние*: На месте\n"
                f"*  Количество ключей*: `{key.count}`\n"
                f"*  Тип ключа*: `{key.key_type}`\n"
                f"*  Тип аппаратный*: `{key.hardware_type}`\n\n"
                f"Нет информации по последнему пользователю\n"
            )

        last_entry = key_entries[-1]
        return await KeyCommandMixin.format_key_entry(last_entry)

    @staticmethod
    async def format_key_entry(entry: sheets.Entry, include_key_info: bool = True) -> str:
        key = keys_table.get_by_name(entry.key_name) if include_key_info else None
        base_info = (
            f"*Ключ*: `{entry.key_name}`\n"
            f"*  Состояние*: {'Не на месте' if entry.time_returned is None else 'Этот ключ сейчас на месте'}\n"
        )

        if key and include_key_info:
            base_info += (
                f"*  Количество ключей*: `{key.count}`\n"
                f"*  Тип ключа*: `{key.key_type}`\n"
                f"*  Тип аппаратный*: `{key.hardware_type}`\n\n"
            )

        if entry.time_returned is None:
            status_info = (
                f"*Ключ выдан:*\n"
                f"  *Имя*: `{entry.emp_firstname} {entry.emp_lastname}`\n"
                f"  *Выдан в*: `{entry.time_received.strftime('%H:%M (%d.%m.%Y)')}`\n"
            )
        else:
            status_info = (
                f"*Последний пользователь:*\n"
                f"  *Имя*: `{entry.emp_firstname} {entry.emp_lastname}`\n"
                f"  *Взял в*: `{entry.time_received.strftime('%H:%M (%d.%m.%Y)')}`\n"
                f"  *Вернул в*: `{entry.time_returned.strftime('%H:%M (%d.%m.%Y)')}`\n"
            )

        additional_info = (
            f"{f'  *Комментарии*: \"{BotUtils.escape_markdown(entry.comment)}\"\n' if entry.comment else ''}"
            f"  *Контакт*: {BotUtils.phone_format(entry.emp_phone)}\n"
        )

        return base_info + status_info + additional_info

    @staticmethod
    async def get_key_history(key_name: str):
        key = keys_table.get_by_name(key_name)
        entries = keys_accounting_table.get_all_entries()
        key_entries = [entry for entry in entries if entry.key_name == key_name]
        response_strs = [""]
        if key:
            response_strs[-1] = (
                f"*Ключ*: `{key_name}`\n"
                f"*Количество ключей*: `{key.count}`\n"
                f"*Тип ключа*: `{key.key_type}`\n"
                f"*Тип аппаратный*: `{key.hardware_type}`\n"
                f"*Этот ключ брали*: {len(key_entries)} раз(а)\n\n"
            )
        else:
            response_strs[-1] = (
                f"*Ключ*: `{key_name}`\n"
                f"*Этот ключ брали*: {len(key_entries)} раз(а)\n\n"
            )
        if not key_entries:
            response_strs[-1] += "По этому ключу нет записей"
            return response_strs
        for entry in key_entries:
            if len(response_strs[-1]) > 2000:
                response_strs.append("")
            response_strs[-1] += (
                f"*Имя*: `{entry.emp_firstname} {entry.emp_lastname}`\n"
                f"| *Взял в*: `{entry.time_received.strftime('%H:%M (%d.%m.%Y)')}`\n"
                f"{f"| *Вернул в*: `{entry.time_returned.strftime('%H:%M (%d.%m.%Y)')}`\n" if entry.time_returned else ""}"
                f"| *Контакт*: {BotUtils.phone_format(entry.emp_phone)}\n"
                f"{f"| *Комментарии*: \"{BotUtils.escape_markdown(entry.comment)}\"\n" if entry.comment else ""}"
            )
            response_strs[-1] += "\n"

        return response_strs

    @staticmethod
    async def get_emp_history(emp_name: str):
        first_name, last_name = emp_name.split(" ", 1)
        emp = emp_table.get_by_name(first_name, last_name)
        entries = keys_accounting_table.get_all_entries()
        emp_entries = []
        for entry in entries:
            if entry.emp_firstname == first_name and entry.emp_lastname == last_name:
                emp_entries.append(entry)
        response_strs = [""]
        if emp:
            tg = await bot.get_chat(emp.telegram)
            response_strs[-1] = (
                f"*Имя*: `{emp.first_name} {emp.last_name}`\n"
                f"*Телефон*: {BotUtils.phone_format(emp.phone_number)}\n"
                f"{f"*Телеграм*: @{tg.username}\n" if tg.username else ""}"
                f"*Роли*: {', '.join(emp.roles) if emp.roles else 'Нет'}\n"
                f"*Этот сотрудник брал ключи*: {len(emp_entries)} раз(а)\n\n"
            )
        else:
            response_strs[-1] = (
                f"*Имя*: `{first_name} {last_name}`\n"
                f"*Этот сотрудник брал ключи*: {len(emp_entries)} раз(а)\n\n"
            )
        if not emp_entries:
            response_strs[-1] += "По этому сотруднику нет записей"
            return response_strs
        for entry in emp_entries:
            if len(response_strs[-1]) > 2000:
                response_strs.append("")
            response_strs[-1] += (
                f"*Ключ*: `{entry.key_name}`\n"
                f"| *Взял в*: `{entry.time_received.strftime('%H:%M (%d.%m.%Y)')}`\n"
                f"{f"| *Вернул в*: `{entry.time_returned.strftime('%H:%M (%d.%m.%Y)')}`\n" if entry.time_returned else ""}"
                f"{f"| *Комментарии*: \"{BotUtils.escape_markdown(entry.comment)}\"\n" if entry.comment else ""}"
            )
            response_strs[-1] += "\n"

        return response_strs

    @staticmethod
    async def get_my_keys(telegram_id: int) -> list[str]:
        user = emp_table.get_by_telegram(telegram_id)
        if not user:
            return []

        not_returned_entries = keys_accounting_table.get_not_returned_keys()
        messages = []
        for entry in not_returned_entries:
            if entry.emp_firstname != user.first_name or entry.emp_lastname != user.last_name:
                continue
            key_data = keys_table.get_by_name(entry.key_name)
            msg = (
                f"*Ключ*: `{entry.key_name}`\n"
                f"| *Взял в*: `{entry.time_received.strftime('%H:%M (%d.%m.%Y)')}`\n"
            )
            if key_data:
                msg += (
                    f"| *Количество ключей*: `{key_data.count}`\n"
                    f"| *Тип ключа*: `{key_data.key_type}`\n"
                    f"| *Тип аппаратный*: `{key_data.hardware_type}`\n"
                )
            if entry.comment:
                msg += f"| *Комментарии*: \"{BotUtils.escape_markdown(entry.comment)}\"\n"
            messages.append(msg)
        if messages: messages.insert(0, f"Ваши активные ключи ({len(messages)})")
        return messages

# endregion

# region Middleware and Error Handling


class LogCommandsMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: Message, data: dict):
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] INFO: Message from {event.from_user.username} ({event.from_user.id}): {event.text}")
        return await handler(event, data)


async def on_error(event: ErrorEvent):
    exc = event.exception
    if isinstance(exc, TelegramForbiddenError):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERR: TelegramForbiddenError: {exc}")
    elif isinstance(exc, TelegramAPIError):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERR: TelegramAPIError: {exc}")
    elif isinstance(exc, ConnectionError):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERR: Connection error")
    elif isinstance(exc, UserNotFoundError):
        await event.update.message.answer("Чтобы использовать бота, нужно зарегистрироваться в системе (/start)")
    else:
        logger.err(exc, additional_text="Error while handling command")
        if hasattr(event, "update") and hasattr(event.update, "message"):
            await event.update.message.answer("Произошла ошибка, попробуйте еще раз")


@dp.startup()
async def on_startup(dispatcher: Dispatcher):  # noqa
    asyncio.create_task(time_reminder())
    print(f"Bot '{(await bot.get_me()).username}' started")


@dp.shutdown()
async def on_shutdown(*args, **kwargs):  # noqa
    print(f"Bot '{(await bot.get_me()).username}' stopped")


# endregion

# region Background Tasks


async def time_reminder():
    while True:
        try:
            print("Checking for time reminders...")
            not_returned_entries = keys_accounting_table.get_not_returned_keys()

            for entry in not_returned_entries:
                print(f"Checking {entry.emp_firstname} {entry.emp_lastname} for key {entry.key_name}")
                if entry.time_received + timedelta(days=3) < datetime.now():
                    emp = emp_table.get_by_name(entry.emp_firstname, entry.emp_lastname)
                    if not emp:
                        print(f"Employee {entry.emp_firstname} {entry.emp_lastname} not found for notification")
                        continue
                    print("Sending notification message")
                    await bot.send_message(
                        chat_id=emp.telegram,
                        text=f"Вы взяли ключ {entry.key_name} 3+ дня назад, но не вернули его. Пожалуйста, верните его в ближайшее время."
                    )
        except ConnectionError:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERR: Connection error")
        except TelegramForbiddenError:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ERR: TelegramForbiddenError, bot blocked by user")
        except Exception as e:
            logger.err(e, "Error in time_reminder")
        await asyncio.sleep(Config.REMINDER_DELAY)


# endregion

# region Registration

class RegistrationState(StatesGroup):
    waiting_for_name = State()
    waiting_for_surname = State()
    waiting_for_phone = State()


@dp.message(Command("start"))
async def send_welcome(message: types.Message, state: FSMContext):
    if await BotUtils.is_registered(message.from_user.id):
        await message.answer("Вы уже зарегистрированы и можете пользоваться ботом!",
                             reply_markup=types.ReplyKeyboardRemove())
        return

    await message.answer("Чтобы зарегистрироваться в системе, введите ваше имя:",
                         reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(RegistrationState.waiting_for_name)


@dp.message(RegistrationState.waiting_for_name)
async def get_name(message: types.Message, state: FSMContext):
    name = message.text.replace(" ", "")
    await state.update_data(name=name)
    await message.answer("Теперь введите вашу фамилию:")
    await state.set_state(RegistrationState.waiting_for_surname)


@dp.message(RegistrationState.waiting_for_surname)
async def get_surname(message: types.Message, state: FSMContext):
    surname = message.text.replace(" ", "")
    await state.update_data(surname=surname)

    markup = BotUtils.make_keyboard([
        [{"text": "Отправить номер телефона", "request_contact": True}],
        [{"text": "Ввести номер телефона вручную"}]
    ])

    await message.answer("Теперь отправьте ваш номер телефона:", reply_markup=markup)
    await state.set_state(RegistrationState.waiting_for_phone)


@dp.message(RegistrationState.waiting_for_phone, F.content_type == ContentType.CONTACT)
async def get_phone_contact(message: Message, state: FSMContext):
    contact = message.contact
    if not contact or message.from_user.id != contact.user_id:
        await message.reply("Пожалуйста, используйте кнопку для отправки вашего номера телефона.")
        return
    await process_phone_number(contact.phone_number, message, state)


@dp.message(RegistrationState.waiting_for_phone, F.content_type == ContentType.TEXT)
async def get_phone_text(message: Message, state: FSMContext):
    phone = message.text
    if not phone.isdigit() or len(phone) < 10 or phone[0] != "7":
        await message.reply("Пожалуйста, введите корректный номер телефона (только цифры) Например: 79008006050.")
        return
    await process_phone_number(phone, message, state)


async def process_phone_number(phone: str, message: Message, state: FSMContext):
    await state.update_data(phone=phone)
    user_data = await state.get_data()

    await message.answer(text="Все данные собраны", reply_markup=types.ReplyKeyboardRemove())

    markup = BotUtils.make_keyboard([[{"text": "Подтвердить", "callback_data": "confirm"}]], inline=True)

    response_text = (
        f"Вот ваши данные:\n"
        f"Имя: {user_data['name']}\n"
        f"Фамилия: {user_data['surname']}\n"
        f"Телефон: {user_data['phone']}\n\n"
        "Если данные не совпадают, начните заново - команда /start."
    )

    await message.reply(response_text, reply_markup=markup)


@dp.callback_query(F.data == "confirm")
async def confirm_data(callback: CallbackQuery, state: FSMContext):
    try:
        user_data = await state.get_data()
        emp_table.new_employee(
            user_data["name"],
            user_data["surname"],
            BotUtils.phone_format(user_data["phone"]),
            callback.from_user.id,
        )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.answer("Данные сохранены, свяжитесь с администратором для получения ролей")
    except Exception as e:
        logger.err(e, "Error in confirm registration data")
        await callback.answer("Произошла ошибка при сохранении данных.")
    finally:
        await state.clear()

# endregion

# region Key Management


class GetKeyState(StatesGroup):
    waiting_for_input = State()
    waiting_for_comment = State()
    waiting_for_confirmation = State()


requested_keys = {}


@dp.message(Command("get_key"))
async def get_key(message: types.Message, state: FSMContext):
    if not await BotUtils.check_permission(message.from_user.id, "user"):
        await message.answer("Вы не имеете доступа к этой команде.")
        return

    emp = emp_table.get_by_telegram(message.from_user.id)
    await state.update_data(emp=emp)
    await message.answer("Введите название ключа или номер базовой станции\n\n(/cancel для отмены)")
    await state.set_state(GetKeyState.waiting_for_input)


@dp.message(GetKeyState.waiting_for_input)
async def get_key_name(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=types.ReplyKeyboardRemove())
        return

    msg = await message.answer("Поиск ключа...", reply_markup=types.ReplyKeyboardRemove())

    exact_key = keys_table.get_by_name(message.text)
    similarities = await KeyCommandMixin.find_similar_keys(message.text)
    not_returned_keys = {key.key_name for key in keys_accounting_table.get_not_returned_keys()}

    if exact_key:
        key_name = exact_key.key_name

    elif len(similarities) == 1:
        key_name = similarities[0]

    elif similarities:
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=sim)] for sim in similarities],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await msg.delete()
        await message.answer("Выберите ключ из найденных:", reply_markup=kb)
        return

    else:
        await msg.delete()
        await message.answer(
            f"Ключ '{message.text}' не найден. Проверьте правильность ввода.",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await state.clear()
        return

    if key_name in not_returned_keys:
        await msg.delete()
        await message.answer(
            await KeyCommandMixin.get_key_state(key_name),
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode="Markdown"
        )
        await state.clear()
        return

    if key_name in requested_keys:
        await msg.delete()
        await message.answer("Этот ключ уже запрошен.")
        await state.clear()
        return

    await state.update_data(key=key_name)
    await msg.delete()
    await message.answer(
        f"Ключ: {key_name}\n"
        f"Теперь введите комментарий\n\n(/empty - без комментария)\n\n(/cancel для отмены)"
    )
    await state.set_state(GetKeyState.waiting_for_comment)


@dp.message(GetKeyState.waiting_for_comment)
async def get_key_comment(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=types.ReplyKeyboardRemove())
        return

    comment = "" if message.text == "/empty" else message.text
    await state.update_data(comment=comment)

    security_emp = emp_table.get_security_employee()
    if not security_emp:
        await message.reply("Охранник не зарегистрирован.")
        await state.clear()
        return

    data = await state.get_data()
    key_name = data["key"]
    emp_from = data["emp"]

    callback_approve = f"approve_key:{message.from_user.id}:{key_name}:{comment}"
    callback_deny = f"deny_key:{message.from_user.id}:{key_name}"

    markup = BotUtils.make_keyboard([
        [{"text": "Подтвердить выдачу ключей", "callback_data": callback_approve}],
        [{"text": "Отклонить", "callback_data": callback_deny}]
    ], inline=True)

    response_text = (
        f"{f'Запрос на выдачу ключей от пользователя @{message.from_user.username}\n' if message.from_user.username else 'Запрос на выдачу ключей\n'}"
        f"Ключ: {key_name}\n"
        f"Имя: {emp_from.first_name} {emp_from.last_name}\n"
        f"{f'Комментарий: {comment}\n\n' if comment else ''}"
        "Подтвердите действие:"
    )

    await bot.send_message(
        chat_id=security_emp.telegram,
        text=response_text,
        reply_markup=markup,
    )

    await message.answer("Запрос отправлен охраннику. Ожидайте подтверждения.")
    await state.clear()
    requested_keys[key_name] = message.from_user.id
    asyncio.create_task(BotUtils.remove_key_after_delay(key_name, requested_keys, Config.REQUEST_DELAY))


@dp.callback_query(F.data.startswith("approve_key"))
async def approve_key(callback: CallbackQuery):
    _, user_id, key_name, comment = callback.data.split(":")
    if key_name not in requested_keys:
        await callback.message.edit_text(callback.message.text + "\n\nВремя запроса истекло")
        return

    emp = emp_table.get_by_telegram(int(user_id))
    keys_accounting_table.new_entry(
        key_name,
        emp.first_name,
        emp.last_name,
        emp.phone_number,
        comment=comment,
    )

    await bot.send_message(chat_id=user_id, text="✔ Охранник подтвердил ваш запрос на выдачу ключей")
    await callback.message.edit_text(callback.message.text + "\n\n✔ Выдача ключа подтверждена")
    requested_keys.pop(key_name, None)


@dp.callback_query(F.data.startswith("deny_key"))
async def deny_key(callback: CallbackQuery):
    _, user_id, key_name = callback.data.split(":")
    await bot.send_message(chat_id=user_id, text="❌ Охранник отклонил ваш запрос на выдачу ключей.")
    await callback.message.edit_text(callback.message.text + "\n\n❌ Вы отклонили запрос на выдачу ключей.")
    requested_keys.pop(key_name, None)

# endregion

# region Key Information Commands


class FindKeyState(StatesGroup):
    waiting_for_input = State()


@dp.message(Command("find_key"))
async def find_key(message: types.Message, state: FSMContext):
    if not await BotUtils.check_permission(message.from_user.id, "user"):
        await message.answer("Вы не имеете доступа к этой команде.")
        return
    await message.answer("Введите название ключа или номер базовой станции\n\n(/cancel для отмены)")
    await state.set_state(FindKeyState.waiting_for_input)


@dp.message(FindKeyState.waiting_for_input)
async def process_find_key(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=types.ReplyKeyboardRemove())
        return

    similarities = await KeyCommandMixin.find_similar_keys(message.text)
    if not similarities:
        await message.answer("Ключ не найден")
        await state.clear()
        return

    if len(similarities) > 1:
        markup = BotUtils.make_keyboard([[sim] for sim in similarities])
        await message.answer("Выберите ключ из найденных:", reply_markup=markup)
        return

    await message.answer(
        await KeyCommandMixin.get_key_state(similarities[0]),
        parse_mode="Markdown",
        reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


class KeyHistoryState(StatesGroup):
    waiting_for_input = State()


@dp.message(Command("key_history"))
async def key_history(message: types.Message, state: FSMContext):
    if not await BotUtils.check_permission(message.from_user.id, "user"):
        await message.answer("Вы не имеете доступа к этой команде.")
        return
    await message.answer("Введите название ключа или номер базовой станции\n\n(/cancel для отмены)")
    await state.set_state(KeyHistoryState.waiting_for_input)


@dp.message(KeyHistoryState.waiting_for_input)
async def process_key_history(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=types.ReplyKeyboardRemove())
        return

    similarities = await KeyCommandMixin.find_similar_keys(message.text)
    if not similarities:
        await message.answer("Ключ не найден")
        await state.clear()
        return

    if len(similarities) > 1:
        markup = BotUtils.make_keyboard([[sim] for sim in similarities])
        await message.answer("Выберите ключ из найденных:", reply_markup=markup)
        return

    history_messages = await KeyCommandMixin.get_key_history(similarities[0])
    for msg in history_messages:
        await message.answer(msg, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


@dp.message(Command("my_keys"))
async def my_keys(message: types.Message):
    if not await BotUtils.check_permission(message.from_user.id, "user"):
        await message.answer("Вы не имеете доступа к этой команде.")
        return

    history_messages = await KeyCommandMixin.get_my_keys(message.from_user.id)
    if not history_messages:
        await message.answer("У вас нет взятых ключей")
        return

    for msg in history_messages:
        await message.answer(msg, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())


class EmpHistoryState(StatesGroup):
    waiting_for_input = State()


@dp.message(Command("emp_history"))
async def emp_history(message: types.Message, state: FSMContext):
    if not await BotUtils.check_permission(message.from_user.id, "user"):
        await message.answer("Вы не имеете доступа к этой команде.")
        return
    await message.answer("Введите ФИ сотрудника для поиска\n\n(/cancel для отмены)")
    await state.set_state(EmpHistoryState.waiting_for_input)


@dp.message(EmpHistoryState.waiting_for_input)
async def process_emp_history(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=types.ReplyKeyboardRemove())
        return

    similarities = await KeyCommandMixin.find_similar_employees(message.text)
    if not similarities:
        await message.answer("Сотрудник не найден")
        await state.clear()
        return

    if len(similarities) > 1:
        markup = BotUtils.make_keyboard([[sim] for sim in similarities])
        await message.answer("Выберите сотрудника из найденных:", reply_markup=markup)
        return

    history_messages = await KeyCommandMixin.get_emp_history(similarities[0])
    for msg in history_messages:
        await message.answer(msg, parse_mode="Markdown", reply_markup=types.ReplyKeyboardRemove())
    await state.clear()


# endregion

# region Security Commands


class ReturnKeyState(StatesGroup):
    waiting_for_input = State()


@dp.message(Command("not_returned"))
async def not_returned(message: types.Message):
    if not await BotUtils.check_permission(message.from_user.id, "security"):
        await message.answer("⛔ Требуются права security")
        return

    try:
        keys = keys_accounting_table.get_not_returned_keys()
        if not keys:
            await message.answer("✅ Все ключи на месте")
            return

        for key in keys:
            emp = emp_table.get_by_name(key.emp_firstname, key.emp_lastname)
            if not emp:
                continue

            markup = BotUtils.make_keyboard([[
                {"text": "Подтвердить возврат",
                 "callback_data": f"return_key:{key.key_name}:{emp.telegram}"}
            ]], inline=True)

            await message.answer(
                await KeyCommandMixin.format_key_entry(key, True),
                reply_markup=markup,
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.err(e, "Error in not_returned")
        await message.answer("⚠ Ошибка при получении списка ключей")


@dp.callback_query(F.data.startswith("return_key"))
async def confirm_return(callback: CallbackQuery):
    if not await BotUtils.check_permission(callback.from_user.id, "security"):
        await callback.answer("⛔ Требуются права security")
        return

    try:
        _, key_name, user_id = callback.data.split(":")
        keys_accounting_table.set_return_time_by_key_name(key_name)

        await bot.send_message(
            chat_id=user_id,
            text=f"✅ Ключ {key_name} возвращен"
        )
        await callback.message.edit_text(
            text=f"{callback.message.text}\n\n✅ Возврат подтвержден",
            reply_markup=None
        )
    except Exception as e:
        logger.err(e, "Error in confirm_return")
        await callback.answer("⚠ Ошибка при подтверждении возврата")


@dp.message(Command("return_key"))
async def return_key_start(message: types.Message, state: FSMContext):
    if not await BotUtils.check_permission(message.from_user.id, "security"):
        await message.answer("⛔ Требуются права security")
        return

    await message.answer(
        "Введите номер ключа или название станции:\n"
        "(/cancel - отмена)",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(ReturnKeyState.waiting_for_input)


@dp.message(ReturnKeyState.waiting_for_input)
async def process_return_key(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("❌ Отменено", reply_markup=types.ReplyKeyboardRemove())
        return

    try:
        msg = await message.answer("🔍 Поиск ключа...")

        # Поиск ключа
        key = keys_table.get_by_name(message.text)
        if not key:
            similarities = await KeyCommandMixin.find_similar_keys(message.text)
            if not similarities:
                await msg.edit_text("🔴 Ключ не найден")
                return
            key = keys_table.get_by_name(similarities[0])

        # Проверка статуса
        entries = keys_accounting_table.get_not_returned_keys()
        entry = next((e for e in entries if e.key_name == key.key_name), None)
        if not entry:
            await msg.edit_text(
                f"Ключ {key.key_name} уже на месте:\n\n" +
                await KeyCommandMixin.get_key_state(key.key_name),
                parse_mode="Markdown"
            )
            return

        # Получаем данные сотрудника
        emp = emp_table.get_by_name(entry.emp_firstname, entry.emp_lastname)
        if not emp:
            await msg.edit_text("⚠ Не удалось найти сотрудника")
            return

        # Формируем подтверждение
        markup = BotUtils.make_keyboard([[
            {"text": "Подтвердить возврат",
             "callback_data": f"return_key:{key.key_name}:{emp.telegram}"}
        ]], inline=True)

        await msg.delete()
        await message.answer(
            await KeyCommandMixin.format_key_entry(entry, True),
            reply_markup=markup,
            parse_mode="Markdown"
        )
        await state.clear()

    except Exception as e:
        logger.err(e, "Error in process_return_key")
        await message.answer("⚠ Ошибка при обработке запроса")
    finally:
        await state.clear()


@dp.message(Command("key_history"))
async def key_history_start(message: types.Message, state: FSMContext):
    if not await BotUtils.check_permission(message.from_user.id, "security"):
        await message.answer("⛔ Требуются права security")
        return

    await message.answer(
        "Введите номер ключа для просмотра истории:\n"
        "(/cancel - отмена)",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.set_state(ReturnKeyState.waiting_for_input)


# endregion

# region Admin Commands


@dp.message(Command("restart"))
async def restart_bot(message: types.Message):
    try:
        if not (await BotUtils.check_permission(message.from_user.id, "admin")):
            await message.answer("Вы не имеете доступа к этой команде.")
            return

        admin = emp_table.get_by_telegram(message.from_user.id)
        logger.log(f"Restart initiated by {admin.first_name} {admin.last_name}")

        await message.answer("♻️ Выполняется перезапуск...")
        await asyncio.sleep(1)

        await dp.storage.close()
        await bot.session.close()

        os.execv(sys.executable, [sys.executable] + sys.argv)

    except Exception as e:
        await message.answer(f"⚠ Ошибка: {str(e)}")
        logger.err(e, "Restart failed")


# endregion

# region Feedback


class FeedbackState(StatesGroup):
    waiting_for_input = State()


@dp.message(Command("feedback"))
async def send_feedback(message: types.Message, state: FSMContext):
    await message.answer("Напишите ваш отзыв или предложение (будет отправлен только текст)\n\n/cancel - отменить")
    await state.set_state(FeedbackState.waiting_for_input)


@dp.message(FeedbackState.waiting_for_input)
async def get_feedback(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await message.answer("Отменено.")
        await state.clear()
        return

    logger.log(
        f"New feedback:\nFrom: {message.from_user.first_name} {message.from_user.last_name} "
        f"(@{message.from_user.username})\n\n```\n{message.text}```")
    await message.answer("Отправлено.")
    await state.clear()


# endregion


@dp.message()
async def echo(message: types.Message):
    await message.answer("Неизвестная команда")


if __name__ == "__main__":
    dp.message.middleware.register(LogCommandsMiddleware())
    asyncio.run(main())

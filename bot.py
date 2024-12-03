import json
from aiogram.enums import ContentType
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup,
    InlineKeyboardButton, Message, ReplyKeyboardRemove)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import CallbackQuery
import asyncio
import sheets
import logger
from datetime import datetime

logger = logger.Logger()
with open("credentials/telegram_bot.json", "r") as f:
    API_TOKEN = json.load(f)["telegram_apikey"]
dp = Dispatcher(storage=MemoryStorage())
bot: Bot = Bot(API_TOKEN)

keys_accounting_table = sheets.KeysAccountingTable()
keys_table = sheets.KeysTable()
emp_table = sheets.EmployeesTable()


def phone_format(x: str | int):
    x = str(x)
    if x.startswith("8") and len(x) == 11:  # 89293232859 -> +79293232859
        x = "+7"+x
    elif not x.startswith("7"):  # 9293232859 -> +79293232859
        x = "+7"+x
    elif not x.startswith("+"):  # 79293232859 -> +79293232859
        x = "+"+x
    return x


async def has_role(role: str, user_id: str):
    user_id = str(user_id)
    employees = await emp_table.get_all_employees()
    emp = next((emp for emp in employees if emp.telegram == user_id), None)
    if emp is not None and role in emp.roles:  # emp found and has enough roles
        return True
    elif emp is None:  # emp not found
        return None
    elif role not in emp.roles:  # emp found, but not enough roles
        return False


async def check_registration(user_id: str) -> bool:
    user_id = str(user_id)
    employees = await emp_table.get_all_employees()
    return any(emp.telegram == user_id for emp in employees)


# region Registration


def needs_registration(user_tag: str) -> bool:
    print(f"User {user_tag} tries to register")
    return True


class RegistrationState(StatesGroup):
    waiting_for_name = State()
    waiting_for_surname = State()
    waiting_for_phone = State()


# Команда /start
@dp.message(Command("start"))
async def send_welcome(message: types.Message, state: FSMContext):
    user_id = message.from_user.id

    keyboard = types.ReplyKeyboardRemove()

    if await check_registration(user_id):
        await message.answer("Вы уже зарегистрированы и можете пользоваться ботом!", reply_markup=keyboard)
        return

    if not user_id: return
    if not needs_registration(user_id): return

    await message.answer("Чтобы зарегистрироваться в системе, введите ваше имя:", reply_markup=keyboard)
    await state.set_state(RegistrationState.waiting_for_name)


@dp.message(RegistrationState.waiting_for_name)
async def get_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Теперь введите вашу фамилию:")
    await state.set_state(RegistrationState.waiting_for_surname)


@dp.message(RegistrationState.waiting_for_surname)
async def get_surname(message: types.Message, state: FSMContext):
    await state.update_data(surname=message.text)
    kb = [
        [KeyboardButton(text="Отправить номер телефона", request_contact=True)],
        [KeyboardButton(text="Ввести номер телефона вручную")]
    ]
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True, keyboard=kb)
    await message.answer("Теперь отправьте ваш номер телефона:", reply_markup=markup)
    await state.set_state(RegistrationState.waiting_for_phone)


@dp.message(RegistrationState.waiting_for_phone, F.content_type == ContentType.CONTACT)
async def get_phone_contact(message: Message, state: FSMContext) -> None:
    contact = message.contact

    if contact is None or message.from_user.id != contact.user_id:
        await message.reply("Пожалуйста, используйте кнопку для отправки вашего номера телефона.")
        return

    await state.update_data(phone=contact.phone_number)
    await finalize_data(message, state)


@dp.message(RegistrationState.waiting_for_phone, F.content_type == ContentType.TEXT)
async def get_phone_text(message: Message, state: FSMContext) -> None:
    phone = message.text

    if not phone.isdigit() or len(phone) < 10 or phone[0] != "7":
        await message.reply("Пожалуйста, введите корректный номер телефона (только цифры) Например: 79008006050.")
        return

    await state.update_data(phone=phone)
    await finalize_data(message, state)


async def finalize_data(message: types.Message, state: FSMContext):
    user_data = await state.get_data()

    await message.answer(text="Все данные собраны", reply_markup=types.ReplyKeyboardRemove())

    kb = [[InlineKeyboardButton(text="Подтвердить", callback_data="confirm")]]
    inline_markup = InlineKeyboardMarkup(inline_keyboard=kb)

    await message.reply(
        f"Вот ваши данные:\n"
        f"Имя: {user_data['name']}\n"
        f"Фамилия: {user_data['surname']}\n"
        f"Телефон: {user_data['phone']}\n\n"
        "Если данные не совпадают, начните заново - команда /start.",
        reply_markup=inline_markup,
    )


@dp.callback_query(F.data == "confirm")
async def confirm_data(callback_query: CallbackQuery, state: FSMContext):
    try:
        user_data = await state.get_data()
        await emp_table.new_employee(
            user_data["name"],
            user_data["surname"],
            phone_format(user_data["phone"]),
            callback_query.from_user.id,
            "user",
        )
    except Exception as e:
        print(e)
        logger.err(e, "Error in confirm registration data")
        await callback_query.answer("Произошла ошибка при сохранении данных.")
        return
    await callback_query.message.edit_reply_markup(reply_markup=None)
    await callback_query.answer("Данные сохранены.")
    await state.clear()


# endregion


# region User Commands


class FindKeyState(StatesGroup):
    waiting_for_key = State()


class GetKeyState(StatesGroup):
    waiting_for_key = State()
    waiting_for_comment = State()
    waiting_for_confirmation = State()


@dp.message(Command("find_key"))
async def find_key(message: types.Message, state: FSMContext):
    if not await has_role("user", message.from_user.id):
        await message.answer("Вы не имеете доступа к этой команде.")
        return

    await message.answer("Введите название или номер ключа для поиска")
    await state.set_state(FindKeyState.waiting_for_key)


@dp.message(Command("get_key"))
async def get_key(message: types.Message, state: FSMContext):
    if not await has_role("user", message.from_user.id):
        await message.answer("Вы не имеете доступа к этой команде.")
        return

    emp = await emp_table.get_by_telegram(message.from_user.id)
    await state.update_data(emp=emp)
    await message.answer("Введите название ключа")
    await state.set_state(GetKeyState.waiting_for_key)


@dp.message(GetKeyState.waiting_for_key)
async def get_key_name(message: types.Message, state: FSMContext):
    msg = await message.answer("Поиск ключа...", reply_markup=types.ReplyKeyboardRemove())
    key_names = {key.key_name for key in await keys_table.get_all_keys()}
    not_returned_keys = {key.key_name for key in await keys_accounting_table.get_not_returned_keys()}
    similarities = await sheets.find_similar(message.text, key_names)
    if message.text in not_returned_keys:
        await msg.delete()
        await message.answer("Этот ключ уже взят")
        await state.clear()
        return
    elif message.text in key_names or len(similarities) == 1:
        if similarities:
            key_name = similarities[0]
        else:
            key_name = message.text
        await state.update_data(key=key_name)
        await msg.delete()
        await message.answer(
            f"Ключ: {key_name}\n"
            f"Теперь введите комментарий")
        await state.set_state(GetKeyState.waiting_for_comment)
        return
    else:
        if similarities:
            kb = []
            for sim in similarities:
                kb.append([KeyboardButton(text=sim)])
            await msg.delete()
            await message.answer("Выберите ключ из найденных:",
                                 reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True,
                                                                  one_time_keyboard=True))
        else:
            await msg.delete()
            await message.answer("Ключ не найден")
            await state.clear()


@dp.message(GetKeyState.waiting_for_comment)
async def get_key_comment(message: types.Message, state: FSMContext):
    await state.update_data(comment=message.text)

    msg = await message.answer("Обработка...")

    security_emp = await emp_table.get_security_employee()
    if not security_emp:
        await message.reply("Охранник не зарегистрирован.")
        await state.clear()
        return

    security_id = security_emp.telegram
    key_name = (await state.get_data())["key"]
    comment = (await state.get_data())["comment"]
    emp_from = (await state.get_data())["emp"]

    callback_data_approve = f"approve_key:{message.from_user.id}:{key_name}:{comment}"
    callback_data_deny = f"deny_key:{message.from_user.id}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Подтвердить выдачу ключей", callback_data=callback_data_approve)],
            [InlineKeyboardButton(text="Отклонить", callback_data=callback_data_deny)]
        ]
    )

    await bot.send_message(
        chat_id=security_id,
        text=(
            f"Запрос на выдачу ключей от пользователя @{message.from_user.username}.\n"
            f"Ключ: {key_name}\n"
            f"Имя: {emp_from.first_name} {emp_from.last_name}\n\n"
            "Подтвердите действие:"
        ),
        reply_markup=keyboard,
    )

    await msg.edit_text("Запрос отправлен охраннику. Ожидайте подтверждения.")
    await state.clear()


@dp.callback_query(F.data.startswith("approve_key"))
async def approve_key(callback: CallbackQuery) -> None:
    _, user_id, key_name, comment = callback.data.split(":")
    emp = await emp_table.get_by_telegram(int(user_id))

    await bot.send_message(
        chat_id=user_id,
        text="Охранник подтвердил выдачу ключей. Можете взять их.",
    )

    await callback.message.edit_text("Вы подтвердили выдачу ключей.")
    await keys_accounting_table.new_entry(
        key_name,
        emp.last_name,
        emp.first_name,
        emp.phone_number,
        comment=comment,
    )


@dp.callback_query(F.data.startswith("deny_key"))
async def deny_key(callback: CallbackQuery) -> None:
    _, user_id = callback.data.split(":")
    await bot.send_message(
        chat_id=user_id,
        text="Охранник отклонил ваш запрос на выдачу ключей.",
    )
    await callback.message.edit_text("Вы отклонили запрос на выдачу ключей.")


def state_format(entry: sheets.Entry) -> str:
    if entry.time_returned is None:
        return (
            f"Ключ: {entry.key_name}\n"
            f"Этот ключ забрал: {entry.emp_firstname} {entry.emp_lastname}\n"
            f"в: {entry.time_received.strftime('%H:%M (%d.%m.%Y)')}\n"
            f"Тел. {phone(entry.emp_phone)}\n"
            f"Комментарии: \"{entry.comment}\""
        )
    else:
        return (
            f"Ключ: {entry.key_name}\n"
            f"Сейчас этот ключ на месте\n\n"
            f"В последний раз его брал: {entry.emp_firstname} {entry.emp_lastname}\n"
            f"Забрал в: {entry.time_received.strftime('%H:%M (%d.%m.%Y)')}\n"
            f"Вернул в: {entry.time_returned.strftime('%H:%M (%d.%m.%Y)')}\n"
            f"Тел. {phone(entry.emp_phone)}\n"
            f"Комментарии: \"{entry.comment}\""
        )


async def get_key_state_str(key_name: str) -> str:
    entries = await keys_accounting_table.get_all_entries()
    key_entries = [entry for entry in entries if entry.key_name == key_name]
    if not key_entries:
        return "По этому ключу пока нет записей, скорее всего он на месте"
    last_entry = key_entries[-1]
    return state_format(last_entry)


@dp.message(FindKeyState.waiting_for_key)
async def waiting_for_key_name(message: types.Message, state: FSMContext):
    msg = await message.answer("Поиск ключа...")
    await state.update_data(key=message.text)
    entries = await keys_accounting_table.get_all_entries()
    keys_obj = await keys_table.get_all_keys()
    key_names = {entry.key_name for entry in entries} | {key.key_name for key in keys_obj}
    similarities = await sheets.find_similar(message.text, key_names)

    if not similarities:
        await msg.edit_text("Ключ не найден")
        await state.clear()
        return

    if len(similarities) > 1:
        kb = []
        for sim in similarities:
            kb.append([KeyboardButton(text=sim)])
        await msg.delete()
        await message.answer("Выберите ключ из найденных:",
                             reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True,
                                                              one_time_keyboard=True))
        # await state.clear()
        return

    await msg.edit_text(await get_key_state_str(similarities[0]))
    await state.clear()


# endregion


# region Security Commands


@dp.message(Command("not_returned"))
async def not_returned(message: types.Message):
    if not await has_role("security", message.from_user.id):
        await message.answer("Вы не имеете доступа к этой команде.")
        return
    msg = await message.answer("Поиск ключей...")

    keys = await keys_accounting_table.get_not_returned_keys()

    if not keys:
        await msg.edit_text("Сейчас все ключи на месте.")
        return

    await msg.delete()

    for key in keys:
        kb = [[InlineKeyboardButton(text="Вернуть", callback_data=f"return_key:{key.key_name}")]]
        await message.answer(state_format(key), reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("return_key"))
async def return_key(callback: CallbackQuery, state: FSMContext):
    key_name = callback.data.split(":")[1]
    await keys_accounting_table.set_return_time_by_key_name(key_name)
    await callback.message.edit_text("Время возврата записано.")


# endregion


async def main():
    print("Starting bot")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

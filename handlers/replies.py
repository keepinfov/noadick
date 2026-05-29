from aiogram.types import Message, User


def reply_target(message: Message) -> User | None:
    """User whose message was explicitly replied to, or None.

    In forum supergroups Telegram sets reply_to_message to the topic root
    (a forum_topic_created service message whose message_id equals the chat's
    message_thread_id) even when the user did not reply to anyone. That implicit
    reply must not be treated as a real reply.
    """
    r = message.reply_to_message
    if r is None or r.from_user is None:
        return None
    if r.forum_topic_created is not None:
        return None
    if message.message_thread_id is not None and r.message_id == message.message_thread_id:
        return None
    return r.from_user

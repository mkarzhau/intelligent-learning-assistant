from telegram import Update
from telegram.ext import CommandHandler, CallbackContext
from services.quiz_service import QuizService

class QuizHandler:
    def __init__(self):
        self.quiz_service = QuizService()

    def start_quiz(self, update: Update, context: CallbackContext):
        chat_id = update.effective_chat.id
        quiz = self.quiz_service.generate_quiz()
        context.bot.send_message(chat_id=chat_id, text=quiz['question'])
        context.user_data['current_quiz'] = quiz

    def answer_quiz(self, update: Update, context: CallbackContext):
        chat_id = update.effective_chat.id
        user_answer = update.message.text
        current_quiz = context.user_data.get('current_quiz')

        if current_quiz:
            correct_answer = current_quiz['answer']
            if user_answer.lower() == correct_answer.lower():
                context.bot.send_message(chat_id=chat_id, text="Correct! 🎉")
            else:
                context.bot.send_message(chat_id=chat_id, text=f"Wrong! The correct answer was: {correct_answer}")
            del context.user_data['current_quiz']
        else:
            context.bot.send_message(chat_id=chat_id, text="Please start a quiz first using /quiz.")

def register_quiz_handlers(dispatcher):
    quiz_handler = QuizHandler()
    dispatcher.add_handler(CommandHandler('quiz', quiz_handler.start_quiz))
    dispatcher.add_handler(CommandHandler('answer', quiz_handler.answer_quiz))
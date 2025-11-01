# Intelligent Learning Assistant Telegram Bot

This project is an Intelligent Learning Assistant Telegram chatbot designed to help university students prepare for exams. The bot automatically generates quizzes, schedules study blocks, and incorporates gamification elements to enhance the learning experience.

## Features

- **Quiz Generation**: Automatically creates quizzes based on study materials.
- **Study Scheduling**: Allows users to schedule study blocks and receive reminders.
- **Gamification**: Engages users with game-like elements to motivate studying.
- **User Management**: Supports user registration and course management.

## Project Structure

```
intelligent-learning-assistant
├── src
│   ├── bot.py                # Main entry point for the Telegram bot
│   ├── handlers
│   │   ├── common.py         # Common command handlers
│   │   ├── quiz.py           # Quiz-related command handlers
│   │   └── schedule.py       # Scheduling command handlers
│   ├── services
│   │   ├── quiz_service.py    # Logic for generating quizzes
│   │   └── schedule_service.py # Logic for scheduling study blocks
│   ├── config.py             # Configuration settings
│   └── models.py             # Database models
├── requirements.txt           # Project dependencies
└── README.md                  # Project documentation
```

## Installation

1. Clone the repository:
   ```
   git clone git@github.com:mkarzhau/intelligent-learning-assistant.git
   cd intelligent-learning-assistant
   ```

2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

3. Configure the bot settings in `src/config.py` with your API keys and database connection settings.

## Usage

1. Run the bot:
   ```
   python src/bot.py
   ```

2. Interact with the bot on Telegram using the commands:
   - `/start`: Start the bot and register.
   - `/add_course <course_name>`: Add a new course to your study plan.
   - `/quiz`: Generate a quiz based on your study materials.
   - `/schedule`: View and manage your study schedule.

## Deployment on Railway

To run your bot 24/7 on Railway:

1. Push your project to GitHub.
2. Create a new project on [Railway](https://railway.app/), and link your GitHub repository.
3. Add a file named `Procfile` in the root of your project with the following content:
    ```
    python src/bot.py
    ```
4. Set up your environment variables (API keys, database connection) in Railway's dashboard.
5. Deploy your project.

Railway will automatically run your bot using the command from the `Procfile`.

## Contributing

Contributions are welcome! Please submit a pull request or open an issue for any suggestions or improvements.

## License

This project is licensed under the MIT License. See the LICENSE file for details.
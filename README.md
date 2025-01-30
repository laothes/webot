# WeBot

A WeChat chatbot powered by Azure OpenAI GPT models.

## Project Structure
```
├── __pycache__
├── templates       
├── __init__.py    
├── bot.py                 # Main implementation - v0.2
├── config_handler.py
├── LICENSE.txt           
├── models.py           
├── README.md             
├── requirements.txt      
├── webot.ico            
├── webot-win 0.1.zip    # .exe v0.1
└── webot-win 0.2.zip    # .exe v0.2
```

## Acknowledgements

Special thanks to the original authors of [deepseek_project](https://github.com/1692775560/deepseek_project) for providing the foundation that made this project possible. This chatbot is modified and enhanced based on their work.

## Azure for Students
Students can get free Azure credits at [Azure for Students](https://azure.microsoft.com/en-us/free/students) to access Azure OpenAI services.

## Installation

You can install all required packages using:

```
pip install -r requirements.txt
```

## Versions

### WeBot v0.2 (Latest)
Updates from v0.1:
- Custom Azure OpenAI API configuration
- Customizable chat settings
- Automatic config.json generation
- Removed built-in Azure OpenAI API

### WeBot v0.1
Initial release with GPT-4 (requires WeChat Pay activation)

## Features

- WeChat message monitoring and auto-reply through ItChat
- Azure OpenAI integration for response generation
- User style learning and mimicking
- Web control panel for configuration
- Local data storage for chat history and user styles

## Usage

1. Run `webot.exe`
2. Scan QR code (in command line or `QR.png`)
3. Wait for web login
4. Open control panel, select user, enable auto-reply

## Generated Files
- `chat_history.db`: Chat records
- `QR.png`: Login QR code
- `user_styles.json`: User styles
- `config.json`: Program settings (v0.2)

## Notes
- WeBot Cannot reply to group messages
- v0.1 API limits: 100,000 tokens/min, 600 requests/min

## License
MIT License - see [LICENSE](LICENSE) file for details.
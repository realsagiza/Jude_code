from setuptools import setup, find_packages

setup(
    name="judecode",
    version="0.1.0",
    description="Jude Code - Your terminal AI coding assistant",
    author="Jude",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.27.0",
        "rich>=13.7.0",
        "pyperclip>=1.8.2",
        "python-dotenv>=1.0.0",
        "mss>=9.0.0",
        "Pillow>=10.0.0",
        "pyautogui>=0.9.54",
    ],
    extras_require={
        "browser": ["playwright>=1.50.0"],
        "full": ["playwright>=1.50.0", "pyppeteer>=0.0.25"],
    },
    entry_points={
        "console_scripts": [
            "judecode=judecode.ui.terminal:main_cli",
        ],
    },
    python_requires=">=3.10, <3.14",  # Python 3.14+ incompatible with pyppeteer
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Operating System :: OS Independent",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: MacOS",
        "Operating System :: POSIX :: Linux",
    ],
)

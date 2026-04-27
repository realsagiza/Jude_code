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
    ],
    entry_points={
        "console_scripts": [
            "judecode=judecode.ui.terminal:main_cli",
        ],
    },
    python_requires=">=3.10",
)

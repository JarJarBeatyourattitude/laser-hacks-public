# IVC Meetup Platform

## Description

A Flask-based web application for organizing and finding study sessions and other meetups, primarily aimed at IVC students. Features include event creation, viewing, commenting, committing to attend, user profiles with preferences, and an AI-powered weekly schedule generator.

## Features

* User Authentication (Signup, Login, Logout) [cite: 107, 109, 110]
* Event Listing and Filtering [cite: 77, 78]
* Manual Event Creation [cite: 90, 91]
* View Events on a Calendar [cite: 153, 155]
* Commit/Uncommit to Events [cite: 100]
* Add Comments to Events [cite: 104, 105]
* User Profiles with Availability and Subject Preferences [cite: 94, 96, 118, 120]
* AI-Powered Weekly Schedule Generation (Admin triggered) [cite: 38, 99]
* Event Image Generation using DALL-E 3 (with semantic reuse check) [cite: 21, 27]

## Prerequisites

* Python 3.x
* `pip` (Python package installer)
* Flask Secret Key (Environment Variable)
* OpenAI API Key (Environment Variable)

## Installation

1.  **Clone the repository (Assuming you have the code):**
    ```bash
    # Navigate to the directory containing the project files
    ```
2.  **Create a virtual environment (Recommended):**
    ```bash
    python -m venv venv
    source venv/bin/activate # On Windows use `venv\Scripts\activate`
    ```
3.  **Install dependencies:**
    *(You will need to create a `requirements.txt` file based on the imports in `app.py`, or install them manually)*
    ```bash
    pip install Flask Flask-SQLAlchemy Flask-Login Werkzeug email-validator openai requests python-dateutil
    ```
    *Note: `python-dateutil` is used for more flexible date parsing[cite: 13, 14].*

## Configuration

This application relies on environment variables for sensitive keys. You **must** set these before running the application.

1.  **Set Flask Secret Key:**
    * This key is used by Flask to secure session data. Generate a strong, random secret key. You can use Python to generate one:
        ```python
        import secrets
        print(secrets.token_hex(16))
        ```
    * Set the environment variable:
        * **Linux/macOS:**
            ```bash
            export FLASK_SECRET_KEY='your_generated_secret_key_here'
            ```
        * **Windows (Command Prompt):**
            ```bash
            set FLASK_SECRET_KEY=your_generated_secret_key_here
            ```
        * **Windows (PowerShell):**
            ```powershell
            $env:FLASK_SECRET_KEY='your_generated_secret_key_here'
            ```
    * The application uses a default **insecure** key if this variable is not set, which should **not** be used in production[cite: 7].

2.  **Set OpenAI API Key:**
    * This key is required for generating event images [cite: 19] and for the AI scheduling feature[cite: 54].
    * Obtain your API key from the OpenAI platform website.
    * Set the environment variable:
        * **Linux/macOS:**
            ```bash
            export OPENAI_API_KEY='your_openai_api_key_here'
            ```
        * **Windows (Command Prompt):**
            ```bash
            set OPENAI_API_KEY=your_openai_api_key_here
            ```
        * **Windows (PowerShell):**
            ```powershell
            $env:OPENAI_API_KEY='your_openai_api_key_here'
            ```
    * The application will print errors and critical features will fail if this key is not set[cite: 19, 113].

## Running the Application

1.  **Ensure your environment variables (`FLASK_SECRET_KEY`, `OPENAI_API_KEY`) are set in your current terminal session.**
2.  **Navigate to the directory containing `app.py`.**
3.  **Activate your virtual environment (if you created one).**
4.  **Run the Flask application:**
    ```bash
    python app.py
    ```
5.  The application will start, initialize the database (`database.db` will be created if it doesn't exist)[cite: 111], and print messages indicating it's running, typically on `http://127.0.0.1:5001/`[cite: 113].

## Usage

1.  **Access the application:** Open your web browser and go to `http://127.0.0.1:5001/`.
2.  **Sign Up / Login:** Create an account or log in using the navigation links[cite: 182, 183, 192, 193].
3.  **Browse Events:** View existing event proposals on the main page[cite: 175]. Filter and sort events as needed[cite: 128].
4.  **View Calendar:** See a calendar view of scheduled events[cite: 176].
5.  **Create Event:** Manually create a new event using the "Create Event" link[cite: 176, 194].
6.  **Commit/Comment:** Commit to attending events you're interested in or add comments[cite: 135, 138].
7.  **Edit Profile:** Update your name, phone (optional), availability preferences, and subjects of interest via the "My Profile" link in the user dropdown[cite: 179, 114]. These preferences are used by the AI scheduler[cite: 36, 39].
8.  **Admin Panel (User ID 1):** If you are logged in as the user with ID 1 (the default admin)[cite: 111, 180], you can access the Admin Panel to trigger the AI weekly scheduler[cite: 157, 158].
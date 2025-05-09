# telegram_ai_bot/Dockerfile

# 1. Use an official Python runtime as a parent image
FROM python:3.13.3-slim-bullseye
# Using -slim for a smaller image size. Choose a Python version compatible with your dependencies.
# bullseye is a stable Debian release.

# 2. Set the working directory in the container
WORKDIR /app

# 3. Set environment variables (optional, but good practice)
ENV PYTHONDONTWRITEBYTECODE 1  # Prevents python from writing .pyc files
ENV PYTHONUNBUFFERED 1         # Force stdin, stdout, and stderr to be unbuffered

# 4. Install system dependencies (if any)
# For example, if your bot needed 'libpq-dev' for PostgreSQL or 'build-essential' for C extensions:
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     libpq-dev \
#     build-essential \
#     && rm -rf /var/lib/apt/lists/*
# For this bot, we probably don't need extra system dependencies beyond what python-slim provides.

# 5. Copy the requirements file and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. Copy the rest of the application code into the container
COPY . .
# This copies everything in the current directory (where Dockerfile is) to /app in the container.
# If you have a .dockerignore file, it will respect that.

# 7. Expose any ports (if your application were a web server, not strictly needed for a polling bot)
# EXPOSE 8080

# 8. Define the command to run your application
# This will be executed when a container is started from the image.
CMD ["python", "main.py"]
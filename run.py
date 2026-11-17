# -*- coding: utf-8 -*-
"""Entry point: flask --app run seed   then   flask --app run run --debug"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True, port=5000)

"""
agent/ — OpsPilot Backend modulaire
Chaque fichier = un service indépendant.

Structure :
    config.py            Variables d'environnement + constantes
    groq_client.py       Connexion Groq API + rate limiter
    prompts.py           System prompts LLM (chat + surveillance)
    chat_history.py      Historique chat + edition messages
    anomaly_detector.py  Detection anomalies toutes ressources
    rules_engine.py      Generation regles IA par LLM
    report_writer.py     Generation et sauvegarde rapports Markdown
    surveillance.py      Thread surveillance + normalisation etat
    websocket_handler.py Handler WebSocket temps reel
    routes.py            Tous les endpoints REST /api/...
    main.py              Point d'entree FastAPI (remplace chat_agent.py)
"""
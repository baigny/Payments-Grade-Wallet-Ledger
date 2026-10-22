def setup_pair(api, amount=50000):
    sender, recipient = api.user("Nila"), api.user("Ishan")
    source, destination = api.wallet(sender), api.wallet(recipient)
    api.fund(source, amount)
    return sender, recipient, source, destination

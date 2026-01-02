class LoginState:
    def __init__(self):
        self.data = {}

    def create(self, login_id, client, qr):
        self.data[login_id] = {
            "client": client,
            "qr": qr,
            "status": "waiting"
        }

    def get(self, login_id):
        return self.data.get(login_id)

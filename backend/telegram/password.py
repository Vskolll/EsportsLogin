def submit_password_stub(password: str):
    """
    ⚠️ В РЕАЛЬНОСТИ:
    - GetPasswordRequest
    - CheckPasswordRequest (SRP)

    Тут НИЧЕГО не делаем
    """
    if not password:
        raise ValueError("Empty password")

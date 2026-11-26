import os

from app import create_app
from app.services.user_store import DuplicateUserError, InvalidUserDataError, create_user


def main():
    email = os.getenv("OMNIS_ADMIN_EMAIL")
    password = os.getenv("OMNIS_ADMIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Set OMNIS_ADMIN_EMAIL and OMNIS_ADMIN_PASSWORD before provisioning an administrator.")

    create_app()
    try:
        user = create_user(email, password, role="admin")
    except DuplicateUserError:
        raise SystemExit("An account already exists for that email.")
    except InvalidUserDataError as error:
        raise SystemExit(str(error))
    print(f"Provisioned administrator: {user['email']}")


if __name__ == "__main__":
    main()

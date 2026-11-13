"""A user.json written before a setting was renamed still sets it."""
from ffast.config import user


def test_an_old_setting_name_sets_the_new_one():
    assert user.upgrade_user_settings({"loupeBondsLenience": 1.3}) == {"bondsLenience": 1.3}


def test_the_new_name_wins_over_the_old_one():
    got = user.upgrade_user_settings({"loupeBondsLenience": 1.3, "bondsLenience": 1.2})
    assert got == {"bondsLenience": 1.2}


def test_the_bond_lenience_has_a_default_under_its_new_name():
    assert user.default["bondsLenience"] == 1.1
    assert "loupeBondsLenience" not in user.default

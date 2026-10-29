"""The desktop's Qt environment setup (main.py) is macOS-only, and never
overrides a display platform the user chose."""
from utils import macosQtEnv


def _site(tmp_path):
    (tmp_path / "PySide6" / "Qt" / "plugins" / "platforms").mkdir(parents=True)
    (tmp_path / "PySide6" / "Qt" / "lib").mkdir(parents=True)
    return [str(tmp_path)]


def test_linux_gets_nothing(tmp_path):
    assert macosQtEnv("linux", {}, _site(tmp_path)) == {}


def test_windows_gets_nothing(tmp_path):
    assert macosQtEnv("win32", {}, _site(tmp_path)) == {}


def test_macos_gets_the_plugin_paths_and_cocoa(tmp_path):
    env = macosQtEnv("darwin", {}, _site(tmp_path))
    plugins = tmp_path / "PySide6" / "Qt" / "plugins"
    assert env["QT_PLUGIN_PATH"] == str(plugins)
    assert env["QT_QPA_PLATFORM_PLUGIN_PATH"] == str(plugins / "platforms")
    assert env["DYLD_LIBRARY_PATH"] == str(tmp_path / "PySide6" / "Qt" / "lib")
    assert env["QT_QPA_PLATFORM"] == "cocoa"


def test_a_platform_the_user_chose_is_kept(tmp_path):
    env = macosQtEnv("darwin", {"QT_QPA_PLATFORM": "offscreen"}, _site(tmp_path))
    assert "QT_QPA_PLATFORM" not in env
    assert "QT_PLUGIN_PATH" in env


def test_a_plugin_path_the_user_set_leaves_everything_alone(tmp_path):
    assert macosQtEnv("darwin", {"QT_PLUGIN_PATH": "/mine"}, _site(tmp_path)) == {}


def test_no_pyside6_gets_nothing(tmp_path):
    assert macosQtEnv("darwin", {}, [str(tmp_path)]) == {}

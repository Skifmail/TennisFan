#!/usr/bin/env python
"""
Скрипт для проверки конфигурации settings.py и диагностики ошибок.
Запустите: python check_settings.py
"""

import os
import sys

# Проверка перед импортом Django
print("=" * 70)
print("ПРОВЕРКА КОНФИГУРАЦИИ SETTINGS.PY")
print("=" * 70)

# Проверка переменных окружения
print("\n1. Переменные окружения:")
cloudinary_url = os.environ.get('CLOUDINARY_URL')
print(f"   CLOUDINARY_URL: {'✅ Установлена' if cloudinary_url else '❌ НЕ установлена'}")

# Попытка импорта Django
print("\n2. Проверка Django:")
try:
    import django
    print(f"   Django версия: {django.VERSION}")
    
    # Проверка версии Django
    if django.VERSION[0] < 4 or (django.VERSION[0] == 4 and django.VERSION[1] < 2):
        print(f"   ⚠️  Внимание: Django {django.get_version()} не поддерживает STORAGES (требуется 4.2+)")
    else:
        print(f"   ✅ Версия Django поддерживает STORAGES")
except ImportError:
    print("   ❌ Django не установлен")
    sys.exit(1)

# Попытка загрузки settings
print("\n3. Загрузка settings.py:")
try:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()
    
    from django.conf import settings
    print("   ✅ settings.py загружен успешно")
    
    # Проверка STORAGES
    print("\n4. Проверка STORAGES:")
    if hasattr(settings, 'STORAGES'):
        print("   ✅ STORAGES определен")
        print(f"   default: {settings.STORAGES.get('default', {}).get('BACKEND', 'Не определен')}")
        print(f"   staticfiles: {settings.STORAGES.get('staticfiles', {}).get('BACKEND', 'Не определен')}")
    else:
        print("   ❌ STORAGES не определен")
    
    # Проверка INSTALLED_APPS
    print("\n5. Проверка INSTALLED_APPS:")
    if 'cloudinary_storage' in settings.INSTALLED_APPS:
        print("   ✅ cloudinary_storage в INSTALLED_APPS")
        staticfiles_index = settings.INSTALLED_APPS.index('django.contrib.staticfiles')
        cloudinary_index = settings.INSTALLED_APPS.index('cloudinary_storage')
        if cloudinary_index < staticfiles_index:
            print("   ✅ Порядок правильный (cloudinary_storage перед staticfiles)")
        else:
            print("   ⚠️  Порядок неправильный (cloudinary_storage должен быть перед staticfiles)")
    else:
        print("   ⚠️  cloudinary_storage НЕ в INSTALLED_APPS")
    
    if 'cloudinary' in settings.INSTALLED_APPS:
        print("   ✅ cloudinary в INSTALLED_APPS")
    else:
        print("   ⚠️  cloudinary НЕ в INSTALLED_APPS")
    
    # Проверка MEDIA_ROOT
    print("\n6. Проверка MEDIA_ROOT:")
    if hasattr(settings, 'MEDIA_ROOT'):
        if cloudinary_url:
            print(f"   ⚠️  MEDIA_ROOT установлен: {settings.MEDIA_ROOT}")
            print("   ⚠️  При использовании Cloudinary MEDIA_ROOT не должен быть установлен")
        else:
            print(f"   ✅ MEDIA_ROOT: {settings.MEDIA_ROOT}")
    else:
        if cloudinary_url:
            print("   ✅ MEDIA_ROOT не установлен (правильно для Cloudinary)")
        else:
            print("   ⚠️  MEDIA_ROOT не установлен (может быть проблемой для локального хранения)")
    
    # Проверка storage
    print("\n7. Проверка default storage:")
    try:
        from django.core.files.storage import default_storage
        print(f"   Storage класс: {type(default_storage).__name__}")
        print(f"   Модуль: {type(default_storage).__module__}")
        
        if 'cloudinary' in type(default_storage).__module__.lower():
            print("   ✅ Используется Cloudinary storage")
        else:
            print("   ⚠️  Используется локальное хранилище")
    except Exception as e:
        print(f"   ❌ Ошибка при проверке storage: {e}")
    
    print("\n" + "=" * 70)
    print("Проверка завершена!")
    print("=" * 70)
    
except Exception as e:
    print(f"   ❌ Ошибка при загрузке settings.py: {e}")
    import traceback
    print("\nДетали ошибки:")
    traceback.print_exc()
    sys.exit(1)

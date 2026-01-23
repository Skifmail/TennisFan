#!/usr/bin/env python
"""
Быстрый тест для проверки работы Cloudinary.
Запустите: python test_cloudinary.py
"""

import os
import sys
import django

# Настройка Django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

print("=" * 60)
print("Тест конфигурации Cloudinary")
print("=" * 60)

# Проверка настроек
print(f"\n1. CLOUDINARY_URL: {'✅ Установлена' if settings.CLOUDINARY_URL else '❌ НЕ установлена'}")

# Check Django 4.2+ STORAGES configuration
if hasattr(settings, 'STORAGES'):
    default_storage_backend = settings.STORAGES.get('default', {}).get('BACKEND', 'NOT SET')
    print(f"2. STORAGES['default']['BACKEND']: {default_storage_backend}")
elif hasattr(settings, 'DEFAULT_FILE_STORAGE'):
    print(f"2. DEFAULT_FILE_STORAGE: {settings.DEFAULT_FILE_STORAGE}")
else:
    print(f"2. Storage config: NOT FOUND")

if hasattr(settings, 'MEDIA_ROOT'):
    print(f"3. MEDIA_ROOT: {settings.MEDIA_ROOT} ⚠️  (не должен быть установлен при использовании Cloudinary)")
else:
    print(f"3. MEDIA_ROOT: Не установлен ✅ (правильно для Cloudinary)")

print(f"4. MEDIA_URL: {settings.MEDIA_URL}")

# Проверка storage
print(f"\n5. Storage класс: {type(default_storage).__name__}")
print(f"   Модуль: {type(default_storage).__module__}")

# Тест загрузки файла
if settings.CLOUDINARY_URL:
    print(f"\n6. Тест загрузки файла в Cloudinary...")
    try:
        # Создаем тестовое изображение вместо текстового файла
        from PIL import Image
        from io import BytesIO
        
        # Создать простое тестовое изображение
        img = Image.new('RGB', (100, 100), color='blue')
        img_io = BytesIO()
        img.save(img_io, 'PNG')
        img_io.seek(0)
        
        test_file = ContentFile(img_io.read(), name='test_cloudinary.png')
        
        # Пытаемся сохранить
        saved_path = default_storage.save('test/test_cloudinary.png', test_file)
        print(f"   ✅ Файл сохранен: {saved_path}")
        
        # Проверяем URL
        file_url = default_storage.url(saved_path)
        print(f"   URL файла: {file_url}")
        
        if 'cloudinary.com' in file_url or 'res.cloudinary.com' in file_url:
            print(f"   ✅ URL указывает на Cloudinary")
        else:
            print(f"   ⚠️  URL не указывает на Cloudinary")
        
        # Удаляем тестовый файл
        try:
            default_storage.delete(saved_path)
            print(f"   ✅ Тестовый файл удален")
        except Exception as e:
            print(f"   ⚠️  Не удалось удалить тестовый файл: {e}")
            
    except Exception as e:
        print(f"   ❌ Ошибка при загрузке: {e}")
        import traceback
        traceback.print_exc()
else:
    print(f"\n6. ⚠️  Cloudinary не настроен, тест загрузки пропущен")

print("\n" + "=" * 60)
print("Рекомендации:")
print("=" * 60)

if not settings.CLOUDINARY_URL:
    print("1. Установите переменную окружения CLOUDINARY_URL")
elif hasattr(settings, 'MEDIA_ROOT'):
    print("1. ⚠️  MEDIA_ROOT установлен - это может мешать работе Cloudinary")
    print("   Убедитесь, что MEDIA_ROOT не устанавливается когда CLOUDINARY_URL установлена")
else:
    print("1. Конфигурация выглядит правильно")
    print("2. Если файлы все еще не загружаются:")
    print("   - Проверьте логи Django на наличие ошибок")
    print("   - Убедитесь, что переменная CLOUDINARY_URL доступна при запуске")
    print("   - Проверьте права доступа к Cloudinary API")
    print("   - Попробуйте перезапустить сервер Django")

print("=" * 60)

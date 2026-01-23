#!/usr/bin/env python
"""
Диагностический скрипт для проверки ошибки 500 на Railway
"""
import os
import sys
import traceback

print("=" * 70)
print("ДИАГНОСТИКА ОШИБКИ 500")
print("=" * 70)

# Check environment variables
print("\n1. Переменные окружения Railway:")
for var in ['CLOUDINARY_URL', 'SECRET_KEY', 'DEBUG', 'ALLOWED_HOSTS']:
    value = os.environ.get(var, 'НЕ УСТАНОВЛЕНА')
    if var == 'CLOUDINARY_URL' and value != 'НЕ УСТАНОВЛЕНА':
        value = value[:30] + '...'
    elif var == 'SECRET_KEY' and value != 'НЕ УСТАНОВЛЕНА':
        value = value[:20] + '...'
    print(f"   {var}: {value}")

# Try to import Django
print("\n2. Импорт Django:")
try:
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    print(f"   ✅ Django {django.get_version()} импортирован")
    
    # Setup Django
    print("\n3. Инициализация Django:")
    try:
        django.setup()
        print("   ✅ Django настроен успешно")
        
        # Check settings
        from django.conf import settings
        
        print("\n4. Проверка конфигурации:")
        print(f"   DEBUG: {settings.DEBUG}")
        print(f"   ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")
        print(f"   CLOUDINARY_URL: {bool(settings.CLOUDINARY_URL)}")
        print(f"   MEDIA_ROOT: {settings.MEDIA_ROOT}")
        
        # Check apps
        print("\n5. INSTALLED_APPS (критичные для Cloudinary):")
        if 'cloudinary_storage' in settings.INSTALLED_APPS:
            idx = settings.INSTALLED_APPS.index('cloudinary_storage')
            print(f"   ✅ cloudinary_storage: позиция {idx}")
        else:
            print(f"   ❌ cloudinary_storage НЕ в INSTALLED_APPS")
        
        if 'cloudinary' in settings.INSTALLED_APPS:
            idx = settings.INSTALLED_APPS.index('cloudinary')
            print(f"   ✅ cloudinary: позиция {idx}")
        else:
            print(f"   ❌ cloudinary НЕ в INSTALLED_APPS")
        
        # Check static files
        print("\n6. Статические файлы:")
        print(f"   STATIC_URL: {settings.STATIC_URL}")
        print(f"   STATIC_ROOT: {settings.STATIC_ROOT}")
        print(f"   STORAGES default: {settings.STORAGES.get('default', {}).get('BACKEND', 'NOT SET')}")
        
        # Try to start the app
        print("\n7. Тест подключения к БД:")
        try:
            from django.db import connection
            connection.ensure_connection()
            print("   ✅ БД доступна")
        except Exception as e:
            print(f"   ⚠️  Проблема с БД: {e}")
        
        # Try to run migrations
        print("\n8. Проверка миграций:")
        from django.core.management import call_command
        try:
            call_command('migrate', '--check', verbosity=0)
            print("   ✅ Все миграции применены")
        except Exception as e:
            print(f"   ⚠️  Проблема с миграциями: {e}")
        
        print("\n" + "=" * 70)
        print("✅ Диагностика завершена - ошибок не найдено")
        print("=" * 70)
        
    except Exception as e:
        print(f"   ❌ Ошибка при настройке Django:")
        print(f"   {type(e).__name__}: {e}")
        traceback.print_exc()
        sys.exit(1)
        
except ImportError as e:
    print(f"   ❌ Django не установлен: {e}")
    sys.exit(1)

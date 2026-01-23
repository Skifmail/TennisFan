#!/usr/bin/env python
"""
Script to test Cloudinary upload functionality
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.core.files.base import ContentFile
from apps.users.models import Player, User
from django.conf import settings

print("=" * 70)
print("ТЕСТ CLOUDINARY UPLOAD")
print("=" * 70)

# Check environment
cloudinary_url = os.environ.get('CLOUDINARY_URL')
print(f"\n1. CLOUDINARY_URL в окружении: {'✅ ДА' if cloudinary_url else '❌ НЕТ'}")
if cloudinary_url:
    # Extract cloud name safely
    try:
        cloud_name = cloudinary_url.split('@')[-1]
        print(f"   Cloud Name: {cloud_name}")
    except:
        pass

# Check Django settings
print(f"\n2. DEFAULT_FILE_STORAGE: {settings.DEFAULT_FILE_STORAGE}")
print(f"   MEDIA_URL: {settings.MEDIA_URL}")
print(f"   MEDIA_ROOT: {settings.MEDIA_ROOT}")

# Check INSTALLED_APPS
print(f"\n3. cloudinary_storage в INSTALLED_APPS: {'✅ ДА' if 'cloudinary_storage' in settings.INSTALLED_APPS else '❌ НЕТ'}")
print(f"   cloudinary в INSTALLED_APPS: {'✅ ДА' if 'cloudinary' in settings.INSTALLED_APPS else '❌ НЕТ'}")

# Test upload with a test user
print("\n4. Тест создания файла:")
print("-" * 70)

try:
    # Create a test file
    from PIL import Image
    from io import BytesIO
    
    # Generate simple test image
    img = Image.new('RGB', (100, 100), color='red')
    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    
    # Try to test storage directly
    from django.core.files.storage import default_storage
    
    test_filename = 'test_cloudinary_upload.png'
    print(f"   Trying to save: {test_filename}")
    
    # Save the file
    saved_path = default_storage.save(test_filename, img_io)
    print(f"   ✅ Файл сохранён: {saved_path}")
    
    # Get the URL
    file_url = default_storage.url(saved_path)
    print(f"   URL: {file_url}")
    
    # Check if it's a Cloudinary URL
    if 'cloudinary' in file_url or 'res.cloudinary.com' in file_url:
        print(f"   ✅ Это URL Cloudinary!")
    elif file_url.startswith('/media/'):
        print(f"   ❌ Это локальный URL (/media/), Cloudinary не работает!")
    
    # Delete test file
    default_storage.delete(saved_path)
    print(f"   Тестовый файл удалён")
    
except Exception as e:
    print(f"   ❌ Ошибка при тесте: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("Результат: Если видны Cloudinary URLs (res.cloudinary.com), значит всё работает!")
print("=" * 70)

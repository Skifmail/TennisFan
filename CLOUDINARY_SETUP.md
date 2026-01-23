# Cloudinary Setup для Railway

## 1. Создание аккаунта Cloudinary

1. Перейди на https://cloudinary.com/users/register/free
2. Зарегистрируйся (можно через GitHub)
3. Подтверди email
4. На дашборде найди **API Key** и **Cloud Name**

## 2. Получение CLOUDINARY_URL

На странице Settings → API Keys копируешь:
- **Cloud Name** (например: `my_cloud`)
- **API Key** (например: `12345678901234`)
- **API Secret** (например: `abcdefghijklmnop`)

Формируешь URL:
```
cloudinary://API_KEY:API_SECRET@CLOUD_NAME
```

Пример:
```
cloudinary://12345678901234:abcdefghijklmnop@my_cloud
```

## 3. Добавляем в Railway

1. На Railway → Variables добавляешь новую переменную:
   - **Key:** `CLOUDINARY_URL`
   - **Value:** `cloudinary://API_KEY:API_SECRET@CLOUD_NAME`

2. Сохраняешь

## 4. Redeploy

Нажимаешь **Redeploy latest** — Django подхватит Cloudinary и всё загруженное в админке пойдёт на Cloudinary.

## 5. Тестирование

Заходишь в админку → загружаешь фото пользователю → файл сохранится в Cloudinary → сразу же будет видно на сайте, даже после редеплоя!

## Плюсы

✅ Файлы не теряются при редеплое  
✅ Автоматическая оптимизация изображений  
✅ Бесплатный план на 25GB  
✅ Работает везде (Railway, Vercel, etc.)

## Если что-то не работает

### Диагностика

Запусти скрипт проверки конфигурации:
```bash
python check_cloudinary.py
```

Скрипт покажет:
- Установлена ли переменная `CLOUDINARY_URL`
- Правильный ли формат URL
- Правильно ли настроены приложения в Django
- Работает ли подключение к Cloudinary

### Проверка вручную

1. **Проверь переменную окружения:**
   ```bash
   echo $CLOUDINARY_URL
   ```
   Должна быть в формате: `cloudinary://API_KEY:API_SECRET@CLOUD_NAME`

2. **Проверь настройки Django:**
   - `CLOUDINARY_URL` установлен в Railway Variables (для продакшена)
   - Формат URL правильный: `cloudinary://KEY:SECRET@CLOUD_NAME`
   - `cloudinary_storage` должен быть в `INSTALLED_APPS` ПЕРЕД `django.contrib.staticfiles`
   - `DEFAULT_FILE_STORAGE` должен быть `cloudinary_storage.storage.MediaCloudinaryStorage`

3. **Проверь логи:**
   - Railway: Deploy → Deploy Logs
   - Локально: вывод `python manage.py runserver`

4. **Проверь права доступа:**
   - Убедись, что API Key и API Secret правильные
   - Проверь, что аккаунт Cloudinary активен

### Частые проблемы

**Проблема:** Файлы сохраняются локально вместо Cloudinary
- **Решение:** Убедись, что `CLOUDINARY_URL` установлена и доступна при запуске Django

**Проблема:** Ошибка "cloudinary_storage is not installed"
- **Решение:** Проверь, что `cloudinary_storage` добавлен в `INSTALLED_APPS` ПЕРЕД `django.contrib.staticfiles`

**Проблема:** Ошибка подключения к Cloudinary
- **Решение:** Проверь правильность API Key, API Secret и Cloud Name в URL

Если логи чистые — значит всё работает! Просто загрузи фото и проверь в админке.

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

Проверь:
1. `CLOUDINARY_URL` установлен в Railway Variables
2. Формат URL правильный: `cloudinary://KEY:SECRET@CLOUD_NAME`
3. Логи Rails на Deploy → Deploy Logs видят ошибку подключения

Если логи чистые — значит всё работает! Просто загрузи фото и проверь в админке.

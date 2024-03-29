import requests
import yaml
import json
from confluent_kafka import Producer
from flask import Flask, request

app = Flask(__name__)


def read_config():
    """Получение настроек из файла"""
    with open('config.yaml') as f:
        read_data = yaml.load(f, Loader=yaml.FullLoader)
        print("Получены настройки из файла")
    return read_data


config = read_config()

# передача продюсеру названия сервера
# чтобы при запуске новой программы парсер не писал, что нет категории
P = Producer({
    'bootstrap.servers': config["KAFKA_BROKER"]})


@app.route('/api/get_info_wb/', methods=['POST'])
def get_info_wb():
    """Получение информации о товарах маркетплейса Wildberries"""
    try:
        url = json.loads(request.data)["url"]
        print(f"Получена ссылка: {url}")
        need_subcategory, page_url_category = find_the_right_category(url)
        # достать необходимые для запроса данные
        shard_key, kind, subject, ext = get_category_data(need_subcategory)
        # сделать запрос и взять первые пять страниц
        getting_product_pages(shard_key, kind, subject, ext, page_url_category)
    except Exception as error:
        return {"Ошибка": error}
    return "Данные отправлены"


def find_the_right_category(url):
    """Найти нужную категорию товаров"""
    page_url_category = []
    if url.find('https://www.wildberries.ru') != -1:
        # pageUrl подкатегории каталога
        # (например, /catalog/elektronika/razvlecheniya-i-gadzhety/igrovye-konsoli/playstation)
        page_url_category.append(url[len(url) - url[::-1].index('https://www.wildberries.ru'[::-1]):])

    # изменить pageUrl подкатегории до pageUrl категории каталога
    count_find_slash = 0
    for i, c in enumerate(page_url_category[0]):
        if c == "/":
            count_find_slash += 1
            if count_find_slash >= 2:
                # pageUrl каждой подкатегории каталога после "/catalog/" (например, /catalog/elektronika)
                page_url_category.append(page_url_category[0][:i])

    need_category = {}
    need_subcategory = {}
    # взять данные из подкатегории для последующего запроса о взятии товаров
    catalog = requests.get('https://catalog.wb.ru/menu/v6/api?lang=ru&locale=ru')
    for category in catalog.json()['data']['catalog']:
        # найти нужную категорию товаров
        if category['pageUrl'] in page_url_category:
            need_category = category
    # найти нужную подкатегорию товаров
    for _ in find_the_right_subcategory(need_category, page_url_category[0]):
        need_subcategory = _
    return need_subcategory, page_url_category[0]


def find_the_right_subcategory(dict_var, page_url_category):
    """Найти нужную подкатегорию товаров"""
    for k, v in dict_var.items():
        if v == page_url_category:
            yield dict_var  # возвращает необходимую категорию
        elif isinstance(v, dict):  # если значение v является словарем
            for id_val in find_the_right_subcategory(v, page_url_category):
                yield id_val
        elif isinstance(v, list):  # если значение v является списком
            for dict_i in v:  # прохожу по каждому элементу списка
                for id_val in find_the_right_subcategory(dict_i, page_url_category):
                    yield id_val


def get_category_data(subcategory):
    """Получение данных о категории товара"""
    if subcategory.get('shardKey') is None:
        return None, None, None, None

    query = subcategory['query']
    query_split = query.split('&')
    kind = ''
    ext = ''
    subject = query_split[0]

    if query.find('ext') != - 1 and query.find('kind') != - 1:
        kind = '&' + query_split[0]
        subject = query_split[1]
        ext = '&' + query_split[2]

    elif query.find('ext') != - 1:
        ext = '&' + query_split[1]

    elif query.find('kind') != - 1:
        kind = '&' + query_split[0]
        subject = query_split[1]

    return subcategory['shardKey'], kind, subject, ext


def getting_product_pages(shard_key, kind, subject, ext, page_url_category):
    """Получить информацию о товарах с первых 5 страниц категории через мобильное API Wildberries"""
    response = requests.get("https://marketing-info.wildberries.ru/marketing-info/api/v6/info?curr=rub")
    client_params = {p.split('=')[0]: p.split('=')[1] for p in response.json()['xClientInfo'].split('&')}
    product_url = ""
    name_topic = config["PRODUCER_DATA_TOPIC"]
    # если данные клиента, такие как emp и version  получены, то передать их в запрос
    if client_params.get('emp') is not None:
        emp = f"emp={client_params['emp']}"
    else:
        emp = ""
    if client_params.get('version') is not None:
        version = f"&version={client_params['version']}"
    else:
        version = ""
    # получить информацию с первых 5 страниц
    for page_number in range(1, 6):
        if page_url_category != '/promotions':
            # dest - это определение региона и центра выдачи товаров, склада (Это может быть направление
            # или область карты, параметры для выборки из бд, пока неясно, что это за координаты/границы)
            # spp - это скидка постоянного покупателя. Величина переменная, которая зависит от размера выкупа,
            # конкретного зарегистрированного покупателя.
            product_url = f"https://catalog.wb.ru/catalog/{shard_key}/catalog?" \
                          f"appType={client_params['appType']}&curr={client_params['curr']}" \
                          f"&dest={client_params['dest']}&{emp}{ext}{kind}&" \
                          f"lang={client_params['lang']}&locale={client_params['locale']}&page={page_number}&" \
                          f"reg={client_params['reg']}&regions={client_params['regions']}&sort=popular&" \
                          f"spp={client_params['spp']}&{subject}{version}"
        elif shard_key is None and subject is None:
            product_url = "https://www.wildberries.ru/promotions"
        response = requests.get(product_url).json()
        print(f"Данные о товарах с {page_number} страницы будут сохранены в топик {name_topic} по адресу "
              f"{config['KAFKA_BROKER']}")
        save_answer_kafka(response, name_topic)


def delivery_report(err, msg):
    """Вызывается один раз для каждого полученного сообщения, чтобы указать результат доставки.
    Запускается с помощью poll() или flush()."""
    if err is not None:
        print('Ошибка доставки сообщения: {}'.format(err))
    else:
        print('Сообщение, доставленно в {} [{}]'.format(msg.topic(), msg.partition()))


def save_answer_kafka(response, name_topic):
    """Сохраняет каждый JSON ответ сервера отдельным сообщением в "сыром виде" в топик **wb-category** в Kafka"""
    # Добавление сообщения в очередь сообщений в топик (отправка брокеру)
    # callback - используется функцией pull или flush для последующего чтения данных отслеживания сообщения:
    # было ли успешно доставлено или нет
    P.produce(name_topic, f'{response}', callback=delivery_report)

    # Дожидается доставки всех оставшихся сообщений и отчета о доставке
    # Если топик не создан, то он создается c 1 партицей по умолчанию (1 копия данных помещенных в топик)
    P.flush()


if __name__ == '__main__':
    # Принимает на вход по API адрес категории на wildberries.ru
    app.run(host=config["WEB_HOST"], debug=True)

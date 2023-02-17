from confluent_kafka import Producer, Consumer
import datetime
import yaml
import logging


def read_config():
    """Получение настроек из файла"""
    with open('config.yaml') as f:
        read_data = yaml.load(f, Loader=yaml.FullLoader)
    return read_data


config = read_config()

logging.basicConfig(filename='log_parser.log', filemode='a',
                    format=config['LOGGING_FORMAT'],
                    datefmt=config['LOGGING_DATEFMT'],
                    level=logging.DEBUG)


def delivery_report(err, msg):
    """Вызывается один раз для каждого полученного сообщения, чтобы указать результат доставки.
    Запускается с помощью poll() или flush()."""
    if err is not None:
        logging.debug('Ошибка доставки сообщения: {}'.format(err))
    else:
        logging.debug('Сообщение, доставленно в {} [{}]'.format(msg.topic(), msg.partition()))


def save_answer_kafka(response, name_topic):
    """Сохранение информации о каждом товаре страницы в топик **wb-products** в Kafka"""
    logging.debug(f"Получена информация о товаре: {response}")
    # передача продюсеру названия сервера
    p = Producer({
        'bootstrap.servers': config["KAFKA_BROKER"]
    })

    # Добавление сообщения в очередь сообщений в топик (отправка брокеру)
    # callback - используется функцией pull или flush для последующего чтения данных отслеживания сообщения:
    # было ли успешно доставлено или нет
    p.produce(name_topic, f'{response}', callback=delivery_report)

    # Дожидается доставки всех оставшихся сообщений и отчета о доставке
    # Если топик не создан, то он создается c 1 партицей по умолчанию (1 копия данных помещенных в топик)
    p.flush()


# Принимает на вход по API адрес категории на wildberries.ru
def get_data_from_topic():
    """Получить сырые данные из топика wb-category"""
    logging.debug("Запуск парсера")
    try:
        c = Consumer({
            'bootstrap.servers': config["KAFKA_BROKER"],
            # 'group.id': 'mygroup',
            'auto.offset.reset': config["AUTO_OFFSET_RESET"]
        })

        c.subscribe([config["PRODUCER_DATA_TOPIC"]])

        while True:
            msg = c.poll(1.0)  # запрашивает данные каждую миллисекунду
            print("Запрос данных")
            if msg is None:
                print("Данных нет")
                continue
            if msg.error():
                print("Ошибка")
                logging.debug("Ошибка при получении странцы с товрами из топика. {}".format(msg.error()))
                continue
            logging.debug(f'Получена страница с товарами: {msg.value()}')
            print('Получена страница с товарами: {}'.format(msg.value()))
            time_of_receipt = datetime.datetime.now()
            parse_products(msg.value(), time_of_receipt, config)
            c.close()
    except Exception as error:
        logging.debug(error)


def parse_products(msg, time_of_receipt, config):
    """Парсинг товаров и отправка данных о каждом товаре в функцию сохранения товаров"""
    msg = msg.decode('utf-8')
    products = eval(msg)['data']['products']
    for product in products:
        save_answer_kafka(
            {"time": time_of_receipt, "id": product['id'], "name": product['name'],
             "price": product['salePriceU'] / 100, "sale": product['sale']}, config["CONSUMER_DATA_TOPIC"])


if __name__ == '__main__':
    get_data_from_topic()

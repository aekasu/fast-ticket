FROM php:8.2-apache

# ── PHP error reporting ────────────────────────────────────────────────────────
# Suppress deprecation warnings that would corrupt HTTP response headers.
RUN echo "error_reporting = E_ALL & ~E_DEPRECATED & ~E_STRICT" > /usr/local/etc/php/conf.d/error_reporting.ini \
    && echo "display_errors = Off" >> /usr/local/etc/php/conf.d/error_reporting.ini

# ── System packages + PHP extensions ──────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpng-dev \
        libjpeg-dev \
        zlib1g-dev \
        libzip-dev \
        libicu-dev \
        libonig-dev \
        cron \
        curl \
        unzip \
    && docker-php-ext-configure gd --with-jpeg \
    && docker-php-ext-install \
        gd \
        mysqli \
        pdo \
        pdo_mysql \
        zip \
        intl \
        mbstring \
        gettext \
    && a2enmod rewrite \
    && rm -rf /var/lib/apt/lists/*

# ── Download osTicket ──────────────────────────────────────────────────────────
ARG OSTICKET_VERSION=1.18.1
RUN curl -fsSL \
        "https://github.com/osTicket/osTicket/releases/download/v${OSTICKET_VERSION}/osTicket-v${OSTICKET_VERSION}.zip" \
        -o /tmp/osticket.zip \
    && unzip -q /tmp/osticket.zip -d /tmp/osticket \
    && cp -r /tmp/osticket/upload/. /var/www/html/ \
    && rm -rf /tmp/osticket /tmp/osticket.zip

# ── Apache virtualhost — allow .htaccess overrides ────────────────────────────
COPY apache.conf /etc/apache2/sites-available/000-default.conf

# ── Bridge plugin ─────────────────────────────────────────────────────────────
# Copy our PHP integration plugin into osTicket's plugin directory.
COPY ../../bridge/ /var/www/html/include/plugins/fastapi_integration/

# ── Cron ──────────────────────────────────────────────────────────────────────
COPY crontab /etc/cron.d/osticket-cron
RUN chmod 0644 /etc/cron.d/osticket-cron \
    && touch /var/log/osticket-cron.log \
    && chown www-data:www-data /var/log/osticket-cron.log

# ── Permissions ───────────────────────────────────────────────────────────────
RUN chown -R www-data:www-data /var/www/html \
    && chmod -R 755 /var/www/html

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 80

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["apache2-foreground"]

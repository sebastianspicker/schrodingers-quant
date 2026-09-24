# Pytest harness running inside the pinned Freqtrade runtime, so tests exercise
# the same Python and Freqtrade version as the trading container. Build context
# is this directory (tests/); the image ref comes from compose.yaml's single
# pinned x-freqtrade-image anchor via the FREQTRADE_IMAGE build arg.
ARG FREQTRADE_IMAGE
FROM ${FREQTRADE_IMAGE}

COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

ENTRYPOINT []

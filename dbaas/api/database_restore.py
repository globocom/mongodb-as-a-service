# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals
from rest_framework import serializers
from maintenance.models import DatabaseRestore
from api.maintenance_base import MaintennanceBaseApi


class DatabaseRestoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = DatabaseRestore
        fields = (
            'id',
            'current_step',
            'status',
            'can_do_retry',
            'database',
            'task',
            'created_at'
        )


class DatabaseRestoreAPI(MaintennanceBaseApi):

    """
    Task API
    """

    model = DatabaseRestore
    serializer_class = DatabaseRestoreSerializer
    filter_fields = (
        'status',
        'can_do_retry',
        'database',
    )

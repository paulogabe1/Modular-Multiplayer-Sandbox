// Fill out your copyright notice in the Description page of Project Settings.

#pragma once

#include "CoreMinimal.h"
#include "Components/ActorComponent.h"
#include "SharedCameraComponent.generated.h"


UCLASS(Blueprintable, ClassGroup=(CameraManager), /*DisplayName="SharedCameraComponent",*/
	meta=(BlueprintSpawnableComponent))
class SHAREDCAMERASYSTEM_API USharedCameraComponent : public UActorComponent
{
	GENERATED_BODY()

public:
	USharedCameraComponent();

	virtual void TickComponent(float DeltaTime, ELevelTick TickType,
	                           FActorComponentTickFunction* ThisTickFunction) override;

protected:
	virtual void BeginPlay() override;

private:
	UPROPERTY()
	APlayerController* PlayerControllerComp;

	UPROPERTY(BlueprintReadOnly, EditInstanceOnly, meta=(AllowPrivateAccess=true), Category="Camera Manager")
	class ASharedCameraManager* SharedCameraManager;
};

// Fill out your copyright notice in the Description page of Project Settings.


#include "SharedCameraComponent.h"
#include "Kismet/GameplayStatics.h"
#include "SharedCameraManager.h"

// Sets default values for this component's properties
USharedCameraComponent::USharedCameraComponent() : PlayerControllerComp(nullptr), SharedCameraManager(nullptr)
{
	PrimaryComponentTick.bCanEverTick = false;
}

void USharedCameraComponent::BeginPlay()
{
	Super::BeginPlay();

	/** Find MainCameraManager & Set */
	ASharedCameraManager* TargetCamera = Cast<ASharedCameraManager>(
		UGameplayStatics::GetActorOfClass(this, ASharedCameraManager::StaticClass()));

	if (TargetCamera)
	{
		SharedCameraManager = TargetCamera;
		if (const APawn* Player = Cast<APawn>(GetOwner())) PlayerControllerComp = Cast<APlayerController>(
			Player->GetController());

			/** The view target is deliberately NOT set here. A pawn's BeginPlay runs before Possess on the
			 *  server, and before the controller has replicated on a client, so at this moment the controller
			 *  usually does not exist. The block that used to sit here moved the view only when it happened
			 *  to be non-null, which is exactly how one machine stayed on the follow camera at launch.
			 *  The owning game now drives the view from its match-wide camera mode, at possession. */
	}

	/** Register on the authority only. Players replicates, so a client-side push would
	 *  also RPC ServerSetPlayer and register the same character a second time. */
	if (SharedCameraManager && GetOwner() && GetOwner()->HasAuthority())
	{
		SharedCameraManager->SetPlayer(GetOwner());
	}
}

void USharedCameraComponent::TickComponent(float DeltaTime, ELevelTick TickType,
                                           FActorComponentTickFunction* ThisTickFunction)
{
	Super::TickComponent(DeltaTime, TickType, ThisTickFunction);
}
